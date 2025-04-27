#!/bin/bash

# PDF Extractor GUI Launcher for macOS
# This script ensures dependencies are installed and launches the GUI application

# Determine script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "PDF Extractor GUI Launcher"
echo "=========================="

# Check if Python is installed
if ! command -v python &> /dev/null; then
    echo "Python is not installed. Please install Python 3.8 or later."
    exit 1
fi

# Check if pip is installed
if ! command -v pip &> /dev/null; then
    echo "pip is not installed. Installing pip..."
    python -m ensurepip --upgrade
fi

# Activate virtual environment if it exists, otherwise set up a new one
if [ -d ".venv" ]; then
    echo "Activating virtual environment..."
    source .venv/bin/activate
else
    echo "Creating virtual environment..."
    python -m venv .venv
    source .venv/bin/activate
fi

# Install required packages
echo "Checking and installing dependencies..."
pip install pymupdf python-dotenv google-generativeai

# Try to install tkinter using pip first
pip install tk

# If tkinter is still not available, suggest system installations
if ! python -c "import tkinter" &> /dev/null; then
    echo "Tkinter is not available. You may need to install it using one of these methods:"
    echo "  1. Install Python from python.org with Tkinter included"
    echo "  2. Run: brew install python-tk@3"
    echo "  3. Run: xcode-select --install"
    read -p "Would you like to try installing with Homebrew? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if command -v brew &> /dev/null; then
            echo "Installing Python-TK via Homebrew..."
            brew install python-tk
        else
            echo "Homebrew not found. Please install Homebrew first:"
            echo "/bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
            exit 1
        fi
    else
        echo "Please install Tkinter manually and try again."
        exit 1
    fi
fi

# Run the GUI application
echo "Launching PDF Extractor GUI..."
python pdf_extractor_gui.py

# Exit cleanly
echo "Application closed."