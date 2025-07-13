import discord
from discord.ext import commands, tasks
from discord.ui import Button, View, Modal, TextInput
from web3 import Web3

# === CONFIG ===
TOKEN = ''  # Replace safely
CHANNEL_ID = 1393869262980124783
VERIFIED_ROLE_ID = 1368997815291871322
NFT_CONTRACT = '0x1ea72dcf86c95597360879ed589c175f9a655a30'
MIN_AMOUNT = 0.01
MONAD_RPC = 'https://testnet-rpc.monad.xyz/'

# === WEB3 SETUP ===
w3 = Web3(Web3.HTTPProvider(MONAD_RPC))

ERC721_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    }
]

# === DISCORD SETUP ===
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

user_wallets = {}  # user_id: wallet_address


# === MODALS ===
class WalletModal(Modal, title="Verify Wallet"):
    wallet = TextInput(label="Enter your Monad testnet wallet address", placeholder="0x...", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        address = self.wallet.value.strip()

        if not w3.is_address(address):
            await interaction.response.send_message("❌ Invalid wallet address.", ephemeral=True)
            return

        user_wallets[interaction.user.id] = Web3.to_checksum_address(address)

        await interaction.response.send_message(
            f"✅ Wallet received: `{address}`\n"
            f"Please send a **self-transaction of exactly {MIN_AMOUNT} MONAD** from `{address}` to itself.\n"
            f"Once done, click the button below to submit your transaction hash.",
            view=HashView(),
            ephemeral=True
        )


class HashModal(Modal, title="Enter Transaction Hash"):
    txhash = TextInput(label="Transaction Hash", placeholder="0x...", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user
        wallet = user_wallets.get(user.id)

        if not wallet:
            await interaction.response.send_message("❌ Wallet address not found. Start again.", ephemeral=True)
            return

        tx_hash = self.txhash.value.strip()

        try:
            tx = w3.eth.get_transaction(tx_hash)
        except Exception:
            await interaction.response.send_message("❌ Could not find transaction on Monad testnet.", ephemeral=True)
            return

        if tx['from'].lower() != wallet.lower() or tx['to'].lower() != wallet.lower():
            await interaction.response.send_message("❌ Transaction must be a self-transfer from and to the same wallet.", ephemeral=True)
            return

        amount = float(w3.from_wei(tx['value'], 'ether'))
        if abs(amount - MIN_AMOUNT) > 1e-6:
            await interaction.response.send_message(f"❌ You sent {amount:.6f} MONAD — must be exactly {MIN_AMOUNT} MONAD.", ephemeral=True)
            return

        # Check NFT
        contract = w3.eth.contract(address=Web3.to_checksum_address(NFT_CONTRACT), abi=ERC721_ABI)
        balance = contract.functions.balanceOf(wallet).call()

        guild = interaction.guild
        member = guild.get_member(user.id)
        role = guild.get_role(VERIFIED_ROLE_ID)

        if balance >= 1:
            if member and role:
                await member.add_roles(role)
                await interaction.response.send_message("✅ NFT verified! You’ve been assigned the verified role.", ephemeral=True)
        else:
            if member and role and role in member.roles:
                await member.remove_roles(role)
            await interaction.response.send_message("❌ NFT not found in your wallet. Role not assigned.", ephemeral=True)


# === BUTTONS ===
class StartView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(Button(label="✅ Verify", custom_id="start_verify"))


class HashView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(Button(label="Submit TX Hash", custom_id="submit_tx"))


# === EVENTS ===
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send(
            "**Click the button below to start NFT verification**",
            view=StartView()
        )
    reverify_users.start()  # Start auto-reverify loop


@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.data.get("custom_id") == "start_verify":
        await interaction.response.send_modal(WalletModal())
    elif interaction.data.get("custom_id") == "submit_tx":
        await interaction.response.send_modal(HashModal())


# === AUTO REVERIFY TASK ===
@tasks.loop(minutes=1)
async def reverify_users():
    print("🔄 Running periodic NFT recheck...")
    for guild in bot.guilds:
        role = guild.get_role(VERIFIED_ROLE_ID)
        if not role:
            continue

        for member in role.members:
            user_id = member.id
            wallet = user_wallets.get(user_id)

            if not wallet:
                continue  # skip if wallet not saved

            try:
                contract = w3.eth.contract(address=Web3.to_checksum_address(NFT_CONTRACT), abi=ERC721_ABI)
                balance = contract.functions.balanceOf(wallet).call()

                if balance < 1 and role in member.roles:
                    await member.remove_roles(role)
                    print(f"❌ Removed role from {member} (no NFT found)")
            except Exception as e:
                print(f"⚠️ Error verifying {member}: {e}")


# === RUN BOT ===
bot.run(TOKEN)
