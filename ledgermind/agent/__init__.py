"""The hand-rolled agent loop and the fact ledger it populates.

The dispatcher registers each tool return to the ledger before appending it to the model
transcript, so the model can never quote a value that was not recorded first.
"""
