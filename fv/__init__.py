"""
Fourth & Value — the measured decision logic, kept out of the Streamlit page.

An explicit __init__.py rather than an implicit namespace package: namespace
packages resolve against the working directory, and a host that runs the app
from a different directory than the one it lives in can silently pick up the
wrong `fv` or none at all.
"""
