"""Solana on-chain sniper agent.

Discovers freshly-launched Solana tokens, screens them for rug/honeypot risk,
snipes qualifying entries through the Jupiter aggregator, and auto-exits at
profit targets, trailing stops, or stop-losses. Paper trading is the default.
"""
