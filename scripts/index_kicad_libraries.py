import os
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_kicad_sym(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    symbols = []

    parts = content.split('(symbol "')
    library_name = Path(file_path).stem

    for part in parts[1:]:
        match = re.match(r'^([^"]+)"', part)
        if not match:
            continue

        symbol_name = match.group(1)

        if "_" in symbol_name and any(x.isdigit() for x in symbol_name.split("_")[-1]):
            if re.search(r'_\d+_\d+$', symbol_name):
                continue

        description = ""
        desc_match = re.search(r'\(property "Description" "([^"]*)"', part)
        if desc_match:
            description = desc_match.group(1)

        keywords = ""
        key_match = re.search(r'\(property "ki_keywords" "([^"]*)"', part)
        if key_match:
            keywords = key_match.group(1)

        pins = []
        pin_matches = re.finditer(r'\(pin\s+(\w+)\s+\w+\s+\(at[^)]+\)\s+\(name\s+"([^"]*)"[^)]*\)\s+\(number\s+"([^"]*)"', part)
        for pm in pin_matches:
            pins.append({
                "name": pm.group(2),
                "number": pm.group(3),
                "electrical_type": pm.group(1),
            })

        symbols.append({
            "exact_name": symbol_name,
            "library": library_name,
            "description": description,
            "keywords": keywords,
            "pins": pins,
        })

    return symbols

#def main():
    search_paths = [
        '/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols',
        '/Users/mehmetkutsalsarsu/digikey-kicad-library'
    ]

def main():
    search_paths = [
        PROJECT_ROOT / 'assets' / 'librarys' / 'kicad_sym',
        PROJECT_ROOT / 'assets' / 'librarys' / 'kicad_lib',
    ]

    all_symbols = []

    for path in search_paths:
        path = Path(path)
        if not path.exists():
            print(f"Path not found: {path}")
            continue

        print(f"Indexing libraries in: {path}")
        for file in os.listdir(path):
            if file.endswith('.kicad_sym'):
                file_path = path / file
                try:
                    symbols = parse_kicad_sym(file_path)
                    all_symbols.extend(symbols)
                    print(f"  Indexed {len(symbols)} symbols from {file}")
                except Exception as e:
                    print(f"  Error parsing {file}: {e}")

    output_path = PROJECT_ROOT / 'assets' / 'kicad_library_index.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(all_symbols, f, indent=2)

    print(f"\nSuccessfully indexed {len(all_symbols)} symbols to {output_path}")


if __name__ == "__main__":
    main()
