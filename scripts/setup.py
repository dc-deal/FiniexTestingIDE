"""
FiniexTestingIDE Setup Script
Initializes the development environment
"""

import os
import sys
from pathlib import Path

def create_directories():
    """Create necessary directories"""
    directories = [
        'data/raw',
        'data/processed', 
        'data/cache',
        'python/tests',
        'logs'
    ]
    
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
        print(f"✓ Created directory: {directory}")

def create_gitkeep_files():
    """Create .gitkeep files for empty directories"""
    gitkeep_dirs = [
        'data/raw',
        'data/processed',
        'data/cache'
    ]
    
    for directory in gitkeep_dirs:
        gitkeep_file = Path(directory) / '.gitkeep'
        gitkeep_file.touch()
        print(f"✓ Created .gitkeep: {gitkeep_file}")

def check_python_version():
    """Check if Python version is compatible"""
    if sys.version_info < (3, 11):
        print("❌ Python 3.11+ required")
        return False
    
    print(f"✓ Python {sys.version_info.major}.{sys.version_info.minor} detected")
    return True

def install_requirements():
    """Install Python requirements"""
    import subprocess
    
    try:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'])
        print("✓ Requirements installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to install requirements: {e}")
        return False

def main():
    """Main setup function"""
    print("🚀 FiniexTestingIDE Setup")
    print("=" * 40)
    
    # Check Python version
    if not check_python_version():
        return False
    
    # Create directories
    create_directories()
    create_gitkeep_files()
    
    # Install requirements
    if not install_requirements():
        return False
    
    print("\n" + "=" * 40)
    print("✅ Setup completed successfully!")
    print("\nNext steps:")
    print("1. Copy TickCollector.mq5 to MetaTrader 5")
    print("2. Run data collection for 2 days") 
    print("3. Execute: python python/tick_importer.py")
    print("4. Start developing your first strategy!")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)