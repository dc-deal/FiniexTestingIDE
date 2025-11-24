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
                 base_classes: List[str], decorators: List[str], docstring: str = None):
        self.name = name
        self.file_path = file_path
        self.line_number = line_number
        self.base_classes = base_classes
        self.decorators = decorators
        self.docstring = docstring
        self.methods = []  # List of tuples: (method_name, docstring)


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

                    # Extrahiere Klassen-Docstring
                    class_docstring = ast.get_docstring(node)

                    class_info = ClassInfo(
                        name=node.name,
                        file_path=str(rel_path),
                        line_number=node.lineno,
                        base_classes=base_classes,
                        decorators=decorators,
                        docstring=class_docstring
                    )

                    # Extrahiere Methoden mit Docstrings
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef):
                            method_docstring = ast.get_docstring(item)
                            class_info.methods.append(
                                (item.name, method_docstring))

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

        # Dokumentations-Statistiken
        total_methods = sum(len(cls.methods) for cls in self.classes)
        documented_classes = sum(1 for cls in self.classes if cls.docstring)
        documented_methods = sum(
            1 for cls in self.classes
            for method_name, docstring in cls.methods
            if docstring
        )

        # Undokumentierte sammeln
        undocumented_classes = [
            cls for cls in self.classes if not cls.docstring]
        undocumented_methods = []
        for cls in self.classes:
            for method_name, docstring in cls.methods:
                if not docstring:
                    undocumented_methods.append((cls, method_name))

        # Vererbungs-Statistiken
        classes_with_inheritance = sum(
            1 for cls in self.classes if cls.base_classes)

        # Top 5 größte Klassen
        largest_classes = sorted(
            self.classes, key=lambda c: len(c.methods), reverse=True)[:5]

        return {
            'total_classes': len(self.classes),
            'total_files': self.files_processed,
            'total_methods': total_methods,
            'documented_classes': documented_classes,
            'documented_methods': documented_methods,
            'undocumented_classes': undocumented_classes,
            'undocumented_methods': undocumented_methods,
            'classes_with_inheritance': classes_with_inheritance,
            'largest_classes': largest_classes,
            'by_directory': dict(by_directory),
            'duplicates': duplicates,
            'errors': self.errors,
            'all_classes': self.classes
        }


def format_statistics_header(report: Dict) -> str:
    """Formatiert die Statistics Overview Section."""
    lines = []
    lines.append("=" * 80)
    lines.append("FINIEXTESTINGIDE - PROJECT STRUCTURE ANALYSIS")
    lines.append("=" * 80)
    lines.append("")
    lines.append("📊 STATISTICS OVERVIEW")
    lines.append("-" * 80)

    # Basis-Statistiken
    lines.append(f"Total Files Analyzed:        {report['total_files']}")
    lines.append(f"Total Directories:           {len(report['by_directory'])}")
    lines.append(f"Total Classes:               {report['total_classes']}")
    lines.append(f"Total Methods:               {report['total_methods']}")

    # Durchschnitt
    avg_methods = report['total_methods'] / \
        report['total_classes'] if report['total_classes'] > 0 else 0
    lines.append(f"Avg Methods per Class:       {avg_methods:.1f}")

    # Dokumentation
    class_coverage = (report['documented_classes'] / report['total_classes']
                      * 100) if report['total_classes'] > 0 else 0
    method_coverage = (report['documented_methods'] / report['total_methods']
                       * 100) if report['total_methods'] > 0 else 0
    lines.append(f"")
    lines.append(f"Documentation Coverage:")
    lines.append(
        f"  Classes:                   {report['documented_classes']}/{report['total_classes']} ({class_coverage:.1f}%)")
    lines.append(
        f"  Methods:                   {report['documented_methods']}/{report['total_methods']} ({method_coverage:.1f}%)")

    # Vererbung
    lines.append(f"")
    lines.append(
        f"Classes with Inheritance:    {report['classes_with_inheritance']}/{report['total_classes']}")
    lines.append(f"Namespace Conflicts:         {len(report['duplicates'])}")

    # Top 5 größte Klassen
    if report['largest_classes']:
        lines.append(f"")
        lines.append(f"Top 5 Largest Classes (by method count):")
        for i, cls in enumerate(report['largest_classes'], 1):
            lines.append(
                f"  {i}. {cls.name:30} {len(cls.methods):3} methods  ({cls.file_path})")

    lines.append("")
    return "\n".join(lines)


def format_tree_output(report: Dict) -> str:
    """Formatiert Report als Baum-Struktur."""
    lines = []

    # Statistics Header
    lines.append(format_statistics_header(report))

    # ====================================================================
    # NAMESPACE-KONFLIKTE
    # ====================================================================
    if report['duplicates']:
        lines.append("")
        lines.append("")
        lines.append("⚠️  NAMESPACE CONFLICTS")
        lines.append("=" * 80)
        lines.append(
            f"{len(report['duplicates'])} duplicate class names found:")
        lines.append("")
        for name, classes in sorted(report['duplicates'].items()):
            lines.append(
                f"🔴 Class '{name}' defined in {len(classes)} locations:")
            for cls in classes:
                lines.append(f"   → {cls.file_path}:{cls.line_number}")
            lines.append("")

    # ====================================================================
    # UNDOKUMENTIERTE CODE
    # ====================================================================
    if report['undocumented_classes'] or report['undocumented_methods']:
        lines.append("")
        lines.append("")
        lines.append("📝 UNDOCUMENTED CODE")
        lines.append("=" * 80)

        if report['undocumented_classes']:
            lines.append(
                f"\n⚠️  Classes without docstrings ({len(report['undocumented_classes'])}):")
            for cls in sorted(report['undocumented_classes'], key=lambda c: c.file_path):
                lines.append(
                    f"   • {cls.name:30} → {cls.file_path}:{cls.line_number}")

        if report['undocumented_methods']:
            lines.append(
                f"\n⚠️  Methods without docstrings ({len(report['undocumented_methods'])}):")
            # Group by class for readability
            by_class = defaultdict(list)
            for cls, method_name in report['undocumented_methods']:
                by_class[cls].append(method_name)

            for cls, methods in sorted(by_class.items(), key=lambda x: x[0].file_path):
                lines.append(f"   • {cls.name} ({len(methods)} methods):")
                for method in sorted(methods)[:10]:  # Limit to first 10
                    lines.append(f"     - {method}()")
                if len(methods) > 10:
                    lines.append(f"     ... and {len(methods) - 10} more")

    # ====================================================================
    # VERZEICHNIS-STRUKTUR MIT KLASSEN
    # ====================================================================
    lines.append("")
    lines.append("")
    lines.append("📁 DIRECTORY STRUCTURE WITH CLASSES")
    lines.append("=" * 80)

    for directory in sorted(report['by_directory'].keys()):
        classes = report['by_directory'][directory]
        lines.append(f"\n📂 {directory}/")
        lines.append(f"   ({len(classes)} classes)")

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

                # Docstring als One-Liner
                doc_info = ""
                if cls.docstring:
                    # Erste Zeile, max 60 Zeichen
                    first_line = cls.docstring.split('\n')[0].strip()
                    if len(first_line) > 60:
                        first_line = first_line[:57] + "..."
                    doc_info = f' | "{first_line}"'
                else:
                    doc_info = ' | ⚠️  NO DOCSTRING'

                lines.append(
                    f"      ├─ class {cls.name}{inheritance}{decorators}{doc_info}")
                lines.append(
                    f"      │  └─ Line {cls.line_number}, {len(cls.methods)} methods")

                # Methoden mit Docstrings
                if cls.methods:
                    for i, (method_name, method_doc) in enumerate(cls.methods):
                        is_last = (i == len(cls.methods) - 1)
                        prefix = "      │     " if not is_last else "      │     "

                        marker = "🔒" if method_name.startswith('_') else "🔓"

                        # Methoden-Docstring als One-Liner
                        method_doc_info = ""
                        if method_doc:
                            first_line = method_doc.split('\n')[0].strip()
                            if len(first_line) > 50:
                                first_line = first_line[:47] + "..."
                            method_doc_info = f' | "{first_line}"'
                        else:
                            method_doc_info = ' | ⚠️'

                        lines.append(
                            f"{prefix}└─ {marker} {method_name}(){method_doc_info}")

    # ====================================================================
    # FEHLER
    # ====================================================================
    if report['errors']:
        lines.append("")
        lines.append("")
        lines.append("⚠️  PARSING ERRORS")
        lines.append("=" * 80)
        for file_path, error in report['errors']:
            lines.append(f"   ❌ {file_path}")
            lines.append(f"      {error}")

    lines.append("")
    lines.append("=" * 80)
    return "\n".join(lines)


def format_detailed_output(report: Dict) -> str:
    """Formatiert detaillierten Report mit allen Methoden und Docstrings."""
    lines = []

    # Statistics Header
    lines.append(format_statistics_header(report))

    lines.append("")
    lines.append("=" * 80)
    lines.append("DETAILED CLASS ANALYSIS")
    lines.append("=" * 80)

    for cls in sorted(report['all_classes'], key=lambda c: (c.file_path, c.line_number)):
        lines.append(f"\n{'=' * 80}")
        lines.append(f"class {cls.name}")

        if cls.base_classes:
            lines.append(f"  Inherits from: {', '.join(cls.base_classes)}")

        if cls.decorators:
            lines.append(f"  Decorators: {', '.join(cls.decorators)}")

        lines.append(f"  File: {cls.file_path}:{cls.line_number}")

        # Klassen-Docstring
        if cls.docstring:
            lines.append(f"\n  Class Docstring:")
            for line in cls.docstring.split('\n'):
                lines.append(f"    {line}")
        else:
            lines.append(f"\n  ⚠️  NO CLASS DOCSTRING")

        # Methoden mit Docstrings
        if cls.methods:
            lines.append(f"\n  Methods ({len(cls.methods)}):")
            for method_name, method_doc in cls.methods:
                marker = "🔒" if method_name.startswith('_') else "🔓"
                lines.append(f"\n    {marker} {method_name}()")

                if method_doc:
                    lines.append(f"       Docstring:")
                    for line in method_doc.split('\n'):
                        lines.append(f"         {line}")
                else:
                    lines.append(f"       ⚠️  NO DOCSTRING")

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
