#!/usr/bin/env python3
"""
SAT-SA — Supervisory Analytics Tool for SOC Assessment.

Launch the application:

    python desktop.py

This is the product entry point. `main.py` runs the same analytics
headless; `app.py` is a legacy Streamlit reference view and is not the
application.
"""

from application.main import main

if __name__ == "__main__":
    main()
