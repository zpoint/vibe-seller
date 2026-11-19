"""Inject-driven anti-detect browser engine (Route B).

Shared machinery for clients that ship no local automation API: drive
the GUI client via a Node inspector + renderer DOM and inject a CDP
debug port into the launched kernel, then reuse the common CDPMuxProxy
path. ``yuehulian`` is the first adapter (see ``app/browser/adapters``).
"""
