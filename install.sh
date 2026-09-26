#!/bin/bash

echo "1. Update sistem..."
sudo apt update -y

echo "2. Install aplikasi sistem dari apt-packages.txt..."
xargs sudo apt install -y < apt-packages.txt

echo "3. Setup environment Python dan install library..."
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt

echo "Selesai!"
