"""System ① main pipeline package (system1-app).

Data flow: sources (fetch) → scoring → three-gate filter → AI review / tagging → Top-20 → DB.
Step 2 lands the "data-source abstraction layer": unified Source interface + registry + HotItem contract.
Step 3 wires in topic mapping; Step 4 brings scoring / AI / persistence end to end.
"""
