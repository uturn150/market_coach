# Faithful ports of public Freqtrade strategies (our simulator, real fees 0.40/0.80; minute-timeframe ones on 6-10 months of recent 1m data, others ~3y hourly/4h)
Heracles 4h -80% | MultiMa 4h -96% | Supertrend 1h -100% | Strategy004 5m -46% | Strategy005 5m -64% | Bandtastic 15m -100% | SwingHighToSky 15m -80% | mabStra 4h -97%
TrendRider 1h -100% | BbandRsi 1h -87% | ClucMay72018 5m -7.8% (+3.4% @0.10%) | CombinedBinHAndCluc 5m -15.6% (+6.7%) | BinHV45 1m -8.8% (-4.1%) | ADXMomentum 1h -100% | AwesomeMacd 1h -97% | AverageStrategy 4h -87% (+28% @0.10%)
CofiBit 5m -100% | EMASkipPump 5m -100% | Low_BB 1m -6.2% (+0.6%) | MACDStrategy 5m -68% | MACDStrategy_crossed 5m -66% | Quickie 5m -70% | Simple 5m -99% | Scalp 1m -100% | CMCWinner 15m -84% | ReinforcedAverage 4h -39% (+32%)
FAdxSma(long) 1h -38% | TrendFollowing(long) 5m -94%
Not portable: GodStra, Diamond, PowerTower (price-scale artifacts). Not yet ported: MultiRSI, ReinforcedQuickie, ReinforcedSmoothScalp, SmoothOperator, TDSequential, TechnicalExample, ASDTSRockwell, FOtt, FSupertrend, FReinforced, FSample, VolatilitySystem, UniversalMACD/Diamond(5m), Hummingbot 3m controllers.

# FINAL (2026-09-21): 46 faithful ports run; 0 of 46 profitable at our real fees (0.40/0.80).
Positive only at 0.10% fees: MultiMa +63%, VolatilitySystem(long) +38%, ReinforcedAverage +32%, AverageStrategy +28%, FSample +4.8%, CombinedBinHAndCluc +6.7%, ClucMay72018 +3.4%, Strategy004 +3.4%, UniversalMACD +2.0%, Low_BB +0.6% (all with big drawdowns except the low-frequency 5m/1m dip ones).
Least bad at our fees: Low_BB 1m -6.2%, ClucMay72018 5m -7.8%, BinHV45 1m -8.8%, CombinedBinHAndCluc -15.6%, UniversalMACD -18.4%, VolatilitySystem -22%, ReinforcedAverage -39%.
Second-to-last batch at our fees: SmoothOperator -98%, FReinforced -100%, ReinforcedSmoothScalp -98.8%, TDSequential -99%, FOtt -100%, FSupertrend -100%, TechnicalExample -100% (380 trades/day), ASDTS -100% (256/day), MultiRSI -99.9%, PowerTower as-written -38%, Diamond as-written -61%.
Hummingbot 3m controllers (long, triple barrier): bollinger_v1 -100% (130/day), macd_bb_v1 -75% (-10% @0.10%), supertrend_v1 -100% (416/day).
Not ported: GodStra (price-scale artifact), ReinforcedQuickie (resample code not in file), Hummingbot stat_arb / grid_strike / pmm* / dman_v3 / bollingrid (need shorts, perps or order-book market making), Freqtrade utility strategies (BreakEven, FixedRiskRewardLoss, CustomStoplossWithPSAR, HourBased, InformativeSample, PatternRecognition, hlhb, Almgren-Chriss/TWAP execution).

## Batch 10 — Freqtrade utility strategies (real fees 0.40/0.80 | 0.10% typical)
- HourBased (1h, 24 coins x 1095d): -100% | -67.5%  (13 trades/day; "buy any hour" + ROI table)
- CustomStoplossWithPSAR (1h): -100% | -87.9%  (24 trades/day)
- hlhb long (4h): -69.2% | -40.1%  (0.95 trades/day; hold +38.6%)
- Strategy001_custom_exit (5m, 27 coins x 301d): -97.5% | -59.5%
- Not ported: BreakEven (no entry rule), FixedRiskRewardLoss (needs per-trade ATR stop level; entry is placeholder 'always buy'), PatternRecognition (needs TA-Lib patterns, brute-force hyperopt placeholder), TWAP/AlmgrenChriss (execution algos, not signals).
