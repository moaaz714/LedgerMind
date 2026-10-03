"""Language model providers behind one interface.

base.py defines the protocol. No provider-specific request or response type may cross
that boundary, so swapping providers stays an afternoon's work.
"""
