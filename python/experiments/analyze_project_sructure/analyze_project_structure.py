#!/usr/bin/env python3
"""
FiniexTestingIDE Project Structure Analyzer
============================================

Erstellt eine strukturierte Übersicht aller Python-Klassen im Projekt.
Zeigt:
- Verzeichnisstruktur
- Alle Klassen mit Vererbung
- Dateigrößen und Zeilenanzahl
- Namespace-Probleme (doppelte Klassennamen)

Verwendung:
    python analyze_project_structure.py [--output FILE] [--detailed]

    # Einfache Übersicht (empfohlen für Refactoring)
    python analyze_project_structure.py

    # Oder mit spezifischem Pfad
    python analyze_project_structure.py --path /pfad/zu/deinem/projekt

    # Detaillierte Ansicht mit allen Methoden
    python analyze_project_structure.py --detailed

    # Eigene Ausgabedatei
    python analyze_project_structure.py --output my_analysis.txt
"""

import ast
import os
from pathlib import Path
from typing import Dict, List, Set, Tuple
from collections import defaultdict
import argparse


class ClassInfo:
    """Information über eine Python-Klasse."""

    def __init__(self, name: str, file_path: str, line_number: int,
                 base_classes: List[str], decorators: List[str]):
        self.name = name
        self.file_path = file_path
        self.line_number = line_number
        self.base_classes = base_classes
        self.decorators = decorators
        self.methods = []


class ProjectAnalyzer:
    """Analysiert Python-Projekt-Struktur."""

    def __init__(self, root_dir: str = "."):
        self.root_dir = Path(root_dir).resolve()
        self.classes: List[ClassInfo] = []
        self.files_processed = 0
        self.errors: List[Tuple[str, str]] = []

    def analyze(self) -> Dict:
        """Analysiert das gesamte Projekt."""
        print(f"🔍 Analysiere Projekt: {self.root_dir}")

        # Finde alle Python-Dateien
        python_files = list(self.root_dir.rglob("*.py"))
        print(f"📁 Gefunden: {len(python_files)} Python-Dateien")

        # Analysiere jede Datei
        for py_file in python_files:
            # Skip virtual environments und build directories
            if any(skip in py_file.parts for skip in ['.venv', 'venv', '__pycache__', 'build', 'dist']):
                continue

            self._analyze_file(py_file)

        print(f"✅ Verarbeitet: {self.files_processed} Dateien")
        print(f"📊 Gefunden: {len(self.classes)} Klassen")

        if self.errors:
            print(
                f"⚠️  Fehler: {len(self.errors)} Dateien konnten nicht geparst werden")

        return self._generate_report()

    def _analyze_file(self, file_path: Path):
        """Analysiert eine einzelne Python-Datei."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            tree = ast.parse(content, filename=str(file_path))

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    # Extrahiere Basisklassen
                    base_classes = []
                    for base in node.bases:
                        if isinstance(base, ast.Name):
                            base_classes.append(base.id)
                        elif isinstance(base, ast.Attribute):
                            # Für Klassen wie abc.ABC
                            parts = []
                            current = base
                            while isinstance(current, ast.Attribute):
                                parts.append(current.attr)
                                current = current.value
                            if isinstance(current, ast.Name):
                                parts.append(current.id)
                            base_classes.append('.'.join(reversed(parts)))

                    # Extrahiere Decorators
                    decorators = []
                    for dec in node.decorator_list:
                        if isinstance(dec, ast.Name):
                            decorators.append(dec.id)
                        elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name):
                            decorators.append(f"{dec.func.id}(...)")

                    # Relative Pfad zum Projekt-Root
                    rel_path = file_path.relative_to(self.root_dir)

                    class_info = ClassInfo(
                        name=node.name,
                        file_path=str(rel_path),
                        line_number=node.lineno,
                        base_classes=base_classes,
                        decorators=decorators
                    )

                    # Extrahiere Methoden
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef):
                            class_info.methods.append(item.name)

                    self.classes.append(class_info)

            self.files_processed += 1

        except SyntaxError as e:
            self.errors.append((str(file_path), f"SyntaxError: {e}"))
        except Exception as e:
            self.errors.append((str(file_path), f"Error: {e}"))

    def _generate_report(self) -> Dict:
        """Generiert Analyse-Report."""
        # Gruppiere nach Verzeichnis
        by_directory = defaultdict(list)
        for cls in self.classes:
            directory = str(Path(cls.file_path).parent)
            by_directory[directory].append(cls)

        # Finde Duplikate (gleiche Klassennamen)
        class_names = defaultdict(list)
        for cls in self.classes:
            class_names[cls.name].append(cls)

        duplicates = {name: classes for name, classes in class_names.items()
                      if len(classes) > 1}

        return {
            'total_classes': len(self.classes),
            'total_files': self.files_processed,
            'by_directory': dict(by_directory),
            'duplicates': duplicates,
            'errors': self.errors,
            'all_classes': self.classes
        }


def format_tree_output(report: Dict) -> str:
    """Formatiert Report als Baum-Struktur."""
    lines = []
    lines.append("=" * 80)
    lines.append("FINIEXTESTINGIDE - PROJECT STRUCTURE ANALYSIS")
    lines.append("=" * 80)
    lines.append(f"\n📊 Statistik:")
    lines.append(f"   Klassen gesamt: {report['total_classes']}")
    lines.append(f"   Dateien analysiert: {report['total_files']}")
    lines.append(f"   Verzeichnisse: {len(report['by_directory'])}")

    # Namespace-Konflikte
    if report['duplicates']:
        lines.append(
            f"\n⚠️  NAMESPACE-KONFLIKTE ({len(report['duplicates'])} gefunden):")
        lines.append("-" * 80)
        for name, classes in sorted(report['duplicates'].items()):
            lines.append(f"\n🔴 Klassenname '{name}' mehrfach verwendet:")
            for cls in classes:
                lines.append(f"   → {cls.file_path}:{cls.line_number}")

    # Verzeichnis-Struktur
    lines.append(f"\n\n📁 VERZEICHNIS-STRUKTUR MIT KLASSEN:")
    lines.append("=" * 80)

    for directory in sorted(report['by_directory'].keys()):
        classes = report['by_directory'][directory]
        lines.append(f"\n📂 {directory}/")
        lines.append(f"   ({len(classes)} Klassen)")

        # Gruppiere nach Datei
        by_file = defaultdict(list)
        for cls in classes:
            by_file[cls.file_path].append(cls)

        for file_path in sorted(by_file.keys()):
            file_classes = by_file[file_path]
            filename = Path(file_path).name
            lines.append(f"\n   📄 {filename}")

            for cls in sorted(file_classes, key=lambda c: c.line_number):
                # Basisklassen
                inheritance = ""
                if cls.base_classes:
                    inheritance = f"({', '.join(cls.base_classes)})"

                # Decorators
                decorators = ""
                if cls.decorators:
                    decorators = f" @{', @'.join(cls.decorators)}"

                lines.append(
                    f"      ├─ class {cls.name}{inheritance}{decorators}")
                lines.append(
                    f"      │  └─ Zeile {cls.line_number}, {len(cls.methods)} Methoden")

    # Fehler
    if report['errors']:
        lines.append(f"\n\n⚠️  FEHLER BEIM PARSEN:")
        lines.append("-" * 80)
        for file_path, error in report['errors']:
            lines.append(f"   ❌ {file_path}")
            lines.append(f"      {error}")

    lines.append("\n" + "=" * 80)
    return "\n".join(lines)


def format_detailed_output(report: Dict) -> str:
    """Formatiert detaillierten Report mit allen Methoden."""
    lines = []
    lines.append("=" * 80)
    lines.append("FINIEXTESTINGIDE - DETAILLIERTE KLASSEN-ANALYSE")
    lines.append("=" * 80)

    for cls in sorted(report['all_classes'], key=lambda c: (c.file_path, c.line_number)):
        lines.append(f"\n{'=' * 80}")
        lines.append(f"class {cls.name}")

        if cls.base_classes:
            lines.append(f"  Erbt von: {', '.join(cls.base_classes)}")

        if cls.decorators:
            lines.append(f"  Decorators: {', '.join(cls.decorators)}")

        lines.append(f"  Datei: {cls.file_path}:{cls.line_number}")

        if cls.methods:
            lines.append(f"  Methoden ({len(cls.methods)}):")
            for method in cls.methods:
                marker = "🔒" if method.startswith('_') else "🔓"
                lines.append(f"    {marker} {method}()")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Analysiert FiniexTestingIDE Projektstruktur'
    )
    parser.add_argument(
        '--output', '-o',
        help='Ausgabedatei (Standard: project_structure.txt)',
        default='project_structure.txt'
    )
    parser.add_argument(
        '--detailed', '-d',
        action='store_true',
        help='Detaillierte Ausgabe mit allen Methoden'
    )
    parser.add_argument(
        '--path', '-p',
        help='Projekt-Root-Verzeichnis (Standard: aktuelles Verzeichnis)',
        default='.'
    )

    args = parser.parse_args()

    # Analysiere Projekt
    analyzer = ProjectAnalyzer(args.path)
    report = analyzer.analyze()

    # Generiere Output
    if args.detailed:
        output = format_detailed_output(report)
    else:
        output = format_tree_output(report)

    # Schreibe in Datei
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(output)

    print(f"\n✅ Analyse gespeichert in: {args.output}")

    # Zeige auch auf Konsole
    print("\n" + output)


if __name__ == "__main__":
    main()
