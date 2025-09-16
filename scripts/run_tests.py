"""
FiniexTestingIDE Test Runner
Führt alle Tests aus und generiert Coverage-Report
"""

import sys
import subprocess
from pathlib import Path

def run_tests():
    """Führt alle Tests aus"""
    print("🧪 FiniexTestingIDE Test Suite")
    print("=" * 40)
    
    # Test-Kommandos
    commands = [
        # Unit Tests mit Coverage
        [
            sys.executable, "-m", "pytest", 
            "python/tests/", 
            "-v", 
            "--cov=python/", 
            "--cov-report=html",
            "--cov-report=term"
        ],
        
        # Code Quality Checks
        [sys.executable, "-m", "black", "python/", "--check"],
        [sys.executable, "-m", "isort", "python/", "--check-only"],
        [sys.executable, "-m", "flake8", "python/"]
    ]
    
    success = True
    
    for i, cmd in enumerate(commands, 1):
        print(f"\n📋 Test {i}/{len(commands)}: {' '.join(cmd[2:4])}")
        print("-" * 40)
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                print("✅ PASSED")
                if result.stdout:
                    print(result.stdout)
            else:
                print("❌ FAILED")
                if result.stderr:
                    print(result.stderr)
                if result.stdout:
                    print(result.stdout)
                success = False
                
        except FileNotFoundError:
            print(f"⚠️  Tool nicht gefunden: {cmd[2]}")
            print("   Führe aus: pip install -r requirements.txt")
            success = False
    
    print("\n" + "=" * 40)
    if success:
        print("🎉 Alle Tests erfolgreich!")
        print("\n📊 Coverage Report: htmlcov/index.html")
    else:
        print("💥 Einige Tests sind fehlgeschlagen!")
        
    return success

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)