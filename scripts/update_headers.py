#!/usr/bin/env python3
"""Script to update Python file headers and imports in the cpex package.

This script:
1. Updates the Location: field in docstring headers to the correct file path
2. Updates internal imports from mcpgateway.plugins.framework to cpex.framework
3. Updates internal imports from mcpgateway.plugins.tools to cpex.tools
4. Preserves external dependencies (mcpgateway.common, mcpgateway.config, etc.)
"""

import re
import sys
from pathlib import Path


# Repo root directory (parent of scripts/)
REPO_ROOT = Path(__file__).parent.parent
CPEX_DIR = REPO_ROOT / "cpex"


def get_correct_location(file_path: Path) -> str:
    """Get the correct Location path for a file relative to repo root."""
    rel_path = file_path.relative_to(REPO_ROOT)
    return f"./{rel_path}"


def update_location_header(content: str, correct_location: str) -> str:
    """Update the Location: field in the docstring header."""
    # Match Location: followed by any path until end of line
    # The pattern handles: """Location: ./some/path.py
    pattern = r'("""Location:\s*)[^\n]+'
    replacement = rf'\1{correct_location}'
    return re.sub(pattern, replacement, content, count=1)


def update_imports(content: str) -> str:
    """Update internal package imports from mcpgateway.plugins to cpex."""
    # Replace mcpgateway.plugins.framework with cpex.framework
    # This handles both import statements and docstring references
    content = re.sub(
        r'\bmcpgateway\.plugins\.framework\b',
        'cpex.framework',
        content
    )

    # Replace mcpgateway.plugins.tools with cpex.tools
    content = re.sub(
        r'\bmcpgateway\.plugins\.tools\b',
        'cpex.tools',
        content
    )

    # Note: We intentionally do NOT replace:
    # - mcpgateway.common (external dependency)
    # - mcpgateway.config (external dependency)
    # - mcpgateway.services (external dependency)
    # - mcpgateway.db (external dependency)
    # - mcpgateway.utils (external dependency)

    return content


def process_file(file_path: Path, dry_run: bool = False) -> tuple[bool, list[str]]:
    """Process a single Python file.

    Returns:
        Tuple of (was_modified, list of changes made)
    """
    changes = []

    try:
        content = file_path.read_text(encoding='utf-8')
    except Exception as e:
        print(f"  Error reading {file_path}: {e}")
        return False, []

    original_content = content

    # Update Location header
    correct_location = get_correct_location(file_path)
    if 'Location:' in content:
        # Extract current location for comparison
        match = re.search(r'"""Location:\s*([^\n]+)', content)
        if match:
            old_location = match.group(1).strip()
            if old_location != correct_location:
                content = update_location_header(content, correct_location)
                changes.append(f"Location: {old_location} -> {correct_location}")

    # Update imports
    # Check for mcpgateway.plugins references before updating
    if 'mcpgateway.plugins' in content:
        old_content = content
        content = update_imports(content)
        if old_content != content:
            # Count the replacements
            framework_count = old_content.count('mcpgateway.plugins.framework') - content.count('mcpgateway.plugins.framework')
            tools_count = old_content.count('mcpgateway.plugins.tools') - content.count('mcpgateway.plugins.tools')
            if framework_count > 0:
                changes.append(f"Replaced {framework_count} 'mcpgateway.plugins.framework' -> 'cpex.framework'")
            if tools_count > 0:
                changes.append(f"Replaced {tools_count} 'mcpgateway.plugins.tools' -> 'cpex.tools'")

    # Write changes if any
    if content != original_content:
        if not dry_run:
            file_path.write_text(content, encoding='utf-8')
        return True, changes

    return False, []


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Update Python file headers and imports in the cpex package"
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be changed without actually modifying files"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show all files processed, not just changed ones"
    )
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN - no files will be modified\n")

    # Find all Python files under cpex/
    python_files = sorted(CPEX_DIR.rglob("*.py"))

    total_files = 0
    modified_files = 0

    print(f"Processing {len(python_files)} Python files in {CPEX_DIR}\n")

    for file_path in python_files:
        total_files += 1
        was_modified, changes = process_file(file_path, dry_run=args.dry_run)

        if was_modified:
            modified_files += 1
            rel_path = file_path.relative_to(REPO_ROOT)
            print(f"{'Would modify' if args.dry_run else 'Modified'}: {rel_path}")
            for change in changes:
                print(f"  - {change}")
        elif args.verbose:
            rel_path = file_path.relative_to(REPO_ROOT)
            print(f"No changes: {rel_path}")

    print(f"\n{'Would modify' if args.dry_run else 'Modified'} {modified_files}/{total_files} files")

    # Check for remaining mcpgateway.plugins references
    print("\n--- Checking for remaining mcpgateway.plugins references ---")
    remaining = []
    for file_path in python_files:
        content = file_path.read_text(encoding='utf-8')
        if 'mcpgateway.plugins' in content:
            rel_path = file_path.relative_to(REPO_ROOT)
            matches = re.findall(r'mcpgateway\.plugins\.[^\s\'"]+', content)
            remaining.append((rel_path, set(matches)))

    if remaining:
        print("WARNING: Found remaining mcpgateway.plugins references:")
        for rel_path, matches in remaining:
            print(f"  {rel_path}:")
            for match in sorted(matches):
                print(f"    - {match}")
    else:
        print("No remaining mcpgateway.plugins references found.")

    # Check for remaining external mcpgateway references (expected)
    print("\n--- External mcpgateway dependencies (expected, not changed) ---")
    external_deps = set()
    for file_path in python_files:
        content = file_path.read_text(encoding='utf-8')
        # Find mcpgateway imports that are NOT mcpgateway.plugins
        matches = re.findall(r'mcpgateway\.(?!plugins)[^\s\'"]+', content)
        external_deps.update(matches)

    if external_deps:
        print("External dependencies preserved:")
        for dep in sorted(external_deps):
            print(f"  - {dep}")

    return 0 if not remaining else 1


if __name__ == "__main__":
    sys.exit(main())
