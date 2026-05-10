#!/usr/bin/env python3
"""
FiniexTestingIDE Project Structure Analyzer
============================================

Creates a structured overview of all Python classes and module-level
functions in the project.
Shows:
- Directory structure
- All classes with inheritance
- Module-level functions (utilities, helpers, loaders)
- File sizes and line counts
- Namespace issues (duplicate class names)

Usage:
    python analyze_project_structure.py [--output FILE] [--detailed]

    # Simple overview (recommended for refactoring)
    python analyze_project_structure.py

    # Or with specific path
    python analyze_project_structure.py --path /path/to/your/project

    # Detailed view with all methods
    python analyze_project_structure.py --detailed

    # Custom output file
    python analyze_project_structure.py --output my_analysis.txt
"""

import ast
import os
from pathlib import Path
from typing import Dict, List, Set, Tuple
from collections import defaultdict
import argparse


class ClassInfo:
    """Information about a Python class."""

    def __init__(self, name: str, file_path: str, line_number: int,
                 base_classes: List[str], decorators: List[str], docstring: str = None):
        self.name = name
        self.file_path = file_path
        self.line_number = line_number
        self.base_classes = base_classes
        self.decorators = decorators
        self.docstring = docstring
        self.methods = []  # List of tuples: (method_name, docstring)


class FunctionInfo:
    """Information about a module-level function (not a class method)."""

    def __init__(self, name: str, file_path: str, line_number: int, docstring: str = None):
        self.name = name
        self.file_path = file_path
        self.line_number = line_number
        self.docstring = docstring


class ProjectAnalyzer:
    """Analyzes Python project structure."""

    def __init__(self, root_dir: str = "."):
        self.root_dir = Path(root_dir).resolve()
        self.classes: List[ClassInfo] = []
        self.functions: List[FunctionInfo] = []
        self.files_processed = 0
        self.errors: List[Tuple[str, str]] = []

    def analyze(self) -> Dict:
        """Analyzes the entire project."""
        print(f"🔍 Analyzing project: {self.root_dir}")

        # Find all Python files
        python_files = list(self.root_dir.rglob("*.py"))
        print(f"📁 Found: {len(python_files)} Python files")

        # Analyze each file
        for py_file in python_files:
            # Skip virtual environments and build directories
            if any(skip in py_file.parts for skip in ['.venv', 'venv', '__pycache__', 'build', 'dist']):
                continue

            self._analyze_file(py_file)

        print(f"✅ Processed: {self.files_processed} files")
        print(f"📊 Found: {len(self.classes)} classes")

        if self.errors:
            print(
                f"⚠️  Errors: {len(self.errors)} files could not be parsed")

        return self._generate_report()

    def _analyze_file(self, file_path: Path):
        """Analyzes a single Python file."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            tree = ast.parse(content, filename=str(file_path))

            # Extract module-level functions (direct children of module, not inside classes)
            class_names_in_file = {
                node.name for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef)
            }
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    func_docstring = ast.get_docstring(node)
                    rel_path = file_path.relative_to(self.root_dir)
                    self.functions.append(FunctionInfo(
                        name=node.name,
                        file_path=str(rel_path),
                        line_number=node.lineno,
                        docstring=func_docstring,
                    ))

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    # Extract base classes
                    base_classes = []
                    for base in node.bases:
                        if isinstance(base, ast.Name):
                            base_classes.append(base.id)
                        elif isinstance(base, ast.Attribute):
                            # For classes like abc.ABC
                            parts = []
                            current = base
                            while isinstance(current, ast.Attribute):
                                parts.append(current.attr)
                                current = current.value
                            if isinstance(current, ast.Name):
                                parts.append(current.id)
                            base_classes.append('.'.join(reversed(parts)))

                    # Extract decorators
                    decorators = []
                    for dec in node.decorator_list:
                        if isinstance(dec, ast.Name):
                            decorators.append(dec.id)
                        elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name):
                            decorators.append(f"{dec.func.id}(...)")

                    # Relative path to project root
                    rel_path = file_path.relative_to(self.root_dir)

                    # Extract class docstring
                    class_docstring = ast.get_docstring(node)

                    class_info = ClassInfo(
                        name=node.name,
                        file_path=str(rel_path),
                        line_number=node.lineno,
                        base_classes=base_classes,
                        decorators=decorators,
                        docstring=class_docstring
                    )

                    # Extract methods with docstrings
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
        """Generates analysis report."""
        # Group by directory
        by_directory = defaultdict(list)
        for cls in self.classes:
            directory = str(Path(cls.file_path).parent)
            by_directory[directory].append(cls)

        # Find duplicates (same class names)
        class_names = defaultdict(list)
        for cls in self.classes:
            class_names[cls.name].append(cls)

        duplicates = {name: classes for name, classes in class_names.items()
                      if len(classes) > 1}

        # Documentation statistics
        total_methods = sum(len(cls.methods) for cls in self.classes)
        documented_classes = sum(1 for cls in self.classes if cls.docstring)
        documented_methods = sum(
            1 for cls in self.classes
            for method_name, docstring in cls.methods
            if docstring
        )

        # Collect undocumented items
        undocumented_classes = [
            cls for cls in self.classes if not cls.docstring]
        undocumented_methods = []
        for cls in self.classes:
            for method_name, docstring in cls.methods:
                if not docstring:
                    undocumented_methods.append((cls, method_name))

        # Inheritance statistics
        classes_with_inheritance = sum(
            1 for cls in self.classes if cls.base_classes)

        # Top 5 largest classes
        largest_classes = sorted(
            self.classes, key=lambda c: len(c.methods), reverse=True)[:5]

        # Group functions by directory
        functions_by_directory = defaultdict(list)
        for fn in self.functions:
            directory = str(Path(fn.file_path).parent)
            functions_by_directory[directory].append(fn)

        return {
            'total_classes': len(self.classes),
            'total_files': self.files_processed,
            'total_methods': total_methods,
            'total_functions': len(self.functions),
            'documented_classes': documented_classes,
            'documented_methods': documented_methods,
            'undocumented_classes': undocumented_classes,
            'undocumented_methods': undocumented_methods,
            'classes_with_inheritance': classes_with_inheritance,
            'largest_classes': largest_classes,
            'by_directory': dict(by_directory),
            'functions_by_directory': dict(functions_by_directory),
            'duplicates': duplicates,
            'errors': self.errors,
            'all_classes': self.classes,
            'all_functions': self.functions,
        }


def format_statistics_header(report: Dict) -> str:
    """Formats the statistics overview section."""
    lines = []
    lines.append("=" * 80)
    lines.append("FINIEXTESTINGIDE - PROJECT STRUCTURE ANALYSIS")
    lines.append("=" * 80)
    lines.append("")
    lines.append("📊 STATISTICS OVERVIEW")
    lines.append("-" * 80)

    # Basic statistics
    lines.append(f"Total Files Analyzed:        {report['total_files']}")
    lines.append(f"Total Directories:           {len(report['by_directory'])}")
    lines.append(f"Total Classes:               {report['total_classes']}")
    lines.append(f"Total Methods:               {report['total_methods']}")
    lines.append(f"Module-Level Functions:      {report['total_functions']}")

    # Average
    avg_methods = report['total_methods'] / \
        report['total_classes'] if report['total_classes'] > 0 else 0
    lines.append(f"Avg Methods per Class:       {avg_methods:.1f}")

    # Documentation
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

    # Inheritance
    lines.append(f"")
    lines.append(
        f"Classes with Inheritance:    {report['classes_with_inheritance']}/{report['total_classes']}")
    lines.append(f"Namespace Conflicts:         {len(report['duplicates'])}")

    # Top 5 largest classes
    if report['largest_classes']:
        lines.append(f"")
        lines.append(f"Top 5 Largest Classes (by method count):")
        for i, cls in enumerate(report['largest_classes'], 1):
            lines.append(
                f"  {i}. {cls.name:30} {len(cls.methods):3} methods  ({cls.file_path})")

    lines.append("")
    return "\n".join(lines)


def format_tree_output(report: Dict) -> str:
    """Formats report as tree structure."""
    lines = []

    # Statistics header
    lines.append(format_statistics_header(report))

    # ====================================================================
    # NAMESPACE CONFLICTS
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
    # UNDOCUMENTED CODE
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
    # DIRECTORY STRUCTURE WITH CLASSES
    # ====================================================================
    lines.append("")
    lines.append("")
    lines.append("📁 DIRECTORY STRUCTURE WITH CLASSES")
    lines.append("=" * 80)

    # Build combined file→items map across all directories
    all_directories = set(report['by_directory'].keys()) | set(report['functions_by_directory'].keys())

    for directory in sorted(all_directories):
        classes = report['by_directory'].get(directory, [])
        functions = report['functions_by_directory'].get(directory, [])
        fn_count = f", {len(functions)} functions" if functions else ""
        lines.append(f"\n📂 {directory}/")
        lines.append(f"   ({len(classes)} classes{fn_count})")

        # Group classes and functions by file
        by_file_classes = defaultdict(list)
        for cls in classes:
            by_file_classes[cls.file_path].append(cls)

        by_file_functions = defaultdict(list)
        for fn in functions:
            by_file_functions[fn.file_path].append(fn)

        all_files = sorted(set(list(by_file_classes.keys()) + list(by_file_functions.keys())))

        for file_path in all_files:
            file_classes = by_file_classes.get(file_path, [])
            file_functions = by_file_functions.get(file_path, [])
            filename = Path(file_path).name
            lines.append(f"\n   📄 {filename}")

            for cls in sorted(file_classes, key=lambda c: c.line_number):
                # Base classes
                inheritance = ""
                if cls.base_classes:
                    inheritance = f"({', '.join(cls.base_classes)})"

                # Decorators
                decorators = ""
                if cls.decorators:
                    decorators = f" @{', @'.join(cls.decorators)}"

                # Docstring as one-liner
                doc_info = ""
                if cls.docstring:
                    # First line, max 60 characters
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

                # Methods with docstrings
                if cls.methods:
                    for i, (method_name, method_doc) in enumerate(cls.methods):
                        is_last = (i == len(cls.methods) - 1)
                        prefix = "      │     " if not is_last else "      │     "

                        marker = "🔒" if method_name.startswith('_') else "🔓"

                        # Method docstring as one-liner
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

            # Module-level functions for this file
            for fn in sorted(file_functions, key=lambda f: f.line_number):
                marker = "🔒" if fn.name.startswith('_') else "🔓"
                doc_info = ""
                if fn.docstring:
                    first_line = fn.docstring.split('\n')[0].strip()
                    if len(first_line) > 60:
                        first_line = first_line[:57] + "..."
                    doc_info = f' | "{first_line}"'
                else:
                    doc_info = ' | ⚠️  NO DOCSTRING'
                lines.append(
                    f"      ├─ {marker} def {fn.name}(){doc_info}")
                lines.append(
                    f"      │  └─ Line {fn.line_number}")

    # ====================================================================
    # PARSING ERRORS
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
    """Formats detailed report with all methods and docstrings."""
    lines = []

    # Statistics header
    lines.append(format_statistics_header(report))

    lines.append("")
    lines.append("=" * 80)
    lines.append("DETAILED CLASS AND FUNCTION ANALYSIS")
    lines.append("=" * 80)

    # Build file → classes and file → functions maps
    by_file_classes = defaultdict(list)
    for cls in report['all_classes']:
        by_file_classes[cls.file_path].append(cls)

    by_file_functions = defaultdict(list)
    for fn in report['all_functions']:
        by_file_functions[fn.file_path].append(fn)

    all_files = sorted(
        set(list(by_file_classes.keys()) + list(by_file_functions.keys()))
    )

    for file_path in all_files:
        file_classes = sorted(by_file_classes.get(file_path, []), key=lambda c: c.line_number)
        file_functions = sorted(by_file_functions.get(file_path, []), key=lambda f: f.line_number)

        # Classes
        for cls in file_classes:
            lines.append(f"\n{'=' * 80}")
            lines.append(f"class {cls.name}")

            if cls.base_classes:
                lines.append(f"  Inherits from: {', '.join(cls.base_classes)}")

            if cls.decorators:
                lines.append(f"  Decorators: {', '.join(cls.decorators)}")

            lines.append(f"  File: {cls.file_path}:{cls.line_number}")

            # Class docstring
            if cls.docstring:
                lines.append(f"\n  Class Docstring:")
                for line in cls.docstring.split('\n'):
                    lines.append(f"    {line}")
            else:
                lines.append(f"\n  ⚠️  NO CLASS DOCSTRING")

            # Methods with docstrings
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

        # Module-level functions
        for fn in file_functions:
            lines.append(f"\n{'=' * 80}")
            marker = "🔒" if fn.name.startswith('_') else "🔓"
            lines.append(f"{marker} def {fn.name}()")
            lines.append(f"  File: {fn.file_path}:{fn.line_number}")

            if fn.docstring:
                lines.append(f"\n  Docstring:")
                for line in fn.docstring.split('\n'):
                    lines.append(f"    {line}")
            else:
                lines.append(f"\n  ⚠️  NO DOCSTRING")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Analyzes FiniexTestingIDE project structure'
    )
    parser.add_argument(
        '--output', '-o',
        help='Output file (default: project_structure.txt)',
        default='project_structure.txt'
    )
    parser.add_argument(
        '--detailed', '-d',
        action='store_true',
        help='Detailed output with all methods'
    )
    parser.add_argument(
        '--path', '-p',
        help='Project root directory (default: current directory)',
        default='.'
    )

    args = parser.parse_args()

    # Analyze project
    analyzer = ProjectAnalyzer(args.path)
    report = analyzer.analyze()

    # Generate output
    if args.detailed:
        output = format_detailed_output(report)
    else:
        output = format_tree_output(report)

    # Write to file
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(output)

    print(f"\n✅ Analysis saved to: {args.output}")

    # Also display on console
    print("\n" + output)


if __name__ == "__main__":
    main()
