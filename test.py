# Given values
debt_total =  615384615  # B (6 decimals)
debt_decimals = 6
collateral_pusd = 800000000  # C (6 decimals)
liquidation_bonus_bps = 300  # 3%
target_cr_bps = 13000  # 1.3 (Assumed from example, since not provided in snippet 2)
token_price_18 = 1000000000000000000  # P (1.0 in 18 decimals)
# Normalize to 18 decimals
B18 = debt_total * (10**(18 - debt_decimals))
C18 = collateral_pusd * (10**(18 - 6))
t = target_cr_bps * 1e14
bonus = liquidation_bonus_bps * 1e14
tokenPrice = token_price_18
# Step-by-step logic from Solidity
# collateralTokens = (C18 * 1e18) / tokenPrice
collateralTokens = (C18 * 10**18) // tokenPrice
# tB = (B18 * t) / 1e18
tB = (B18 * int(t)) // 10**18
# numerator = tB - collateralTokens
numerator = tB - collateralTokens
# denominator = t - 1e18 - bonus
denominator = int(t) - 10**18 - int(bonus)
# x18 = (numerator * 1e18) / denominator
if numerator > 0 and denominator > 0:
    x18 = (numerator * 10**18) // denominator
    x = x18 // (10**(18 - debt_decimals))
else:
    x18 = 0
    x = 0
# rewardPUSD calculation
# rewardPUSDRaw = (x * (10000 + bonusBps) * tokenPrice) / (10000 * 1e18)
rewardPUSDRaw = (x * (10000 + liquidation_bonus_bps) * tokenPrice) // (10000 * 10**18)
# rewardPUSD = (rewardPUSDRaw * 1e6) / (10 ** debtDecimals)
rewardPUSD = (rewardPUSDRaw * 10**6) // (10**debt_decimals)

# print(f"{B18=}")
# print(f"{C18=}")
# print(f"{t=}")
# print(f"{bonus=}")
# print(f"{collateralTokens=}")
# print(f"{tB=}")
# print(f"{numerator=}")
# print(f"{denominator=}")
# print(f"{x18=}")
# print(f"{x=}")
# print(f"{rewardPUSD=}")

print("\n=== 计算结果（人类可读格式）===")
print(f"债务总额 (B18): {B18 / 10**18:,.6f} (原始值: {B18:,})")
print(f"抵押品总额 (C18): {C18 / 10**18:,.6f} (原始值: {C18:,})")
print(f"目标抵押率 (t): {t / 10**14:,.6f} (原始值: {t:,.0f})")
print(f"清算奖励 (bonus): {bonus / 10**18:,.6f} (原始值: {bonus:,.0f})")
print(f"抵押代币数量 (collateralTokens): {collateralTokens / 10**18:,.6f} (原始值: {collateralTokens:,})")
print(f"目标债务值 (tB): {tB / 10**18:,.6f} (原始值: {tB:,})")
print(f"分子 (numerator): {numerator / 10**18:,.6f} (原始值: {numerator:,})")
print(f"分母 (denominator): {denominator / 10**18:,.6f} (原始值: {denominator:,})")
print(f"清算金额 (x18): {x18 / 10**18:,.6f} (原始值: {x18:,})")
print(f"清算金额 (x): {x / 10**6:,.6f} (原始值: {x:,}, 精度: 10^6)")
print(f"奖励PUSD (rewardPUSD): {rewardPUSD / 10**6:,.6f} (原始值: {rewardPUSD:,}, 精度: 10^6)")
print("=" * 60)

