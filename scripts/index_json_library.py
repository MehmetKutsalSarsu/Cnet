import os
import json
from pathlib import Path


def _load_kicad_library_lookup(kicad_index_path: str) -> dict[str, str]:

    if not os.path.exists(kicad_index_path):
        return {}
    with open(kicad_index_path, "r", encoding="utf-8") as f:
        kicad_symbols = json.load(f)
    return {sym["exact_name"]: sym["library"] for sym in kicad_symbols}


def parse_json_symbol(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

     # { "type": "kicad_symbol_lib", "content": [ ..., { "type": "symbol", "content": [...] } ] }

    symbols = []

    def find_symbols(obj):
        if isinstance(obj, dict):
            if obj.get('type') == 'symbol':
                symbols.append(obj)
            elif 'content' in obj:
                find_symbols(obj['content'])
        elif isinstance(obj, list):
            for item in obj:
                find_symbols(item)

    find_symbols(data)

    extracted = []
    for sym in symbols:
        name = sym['content'][0] if sym['content'] else "Unknown"

        if "_" in str(name) and str(name).split("_")[-1].isdigit():
            continue

        description = ""
        keywords = ""
        pins = []

        def process_content(content):
            nonlocal description, keywords
            for item in content:
                if isinstance(item, dict):
                    if item.get('type') == 'property':
                        prop_name = item['content'][0]
                        prop_val = item['content'][1]
                        if prop_name == "Description":
                            description = prop_val
                        elif prop_name == "ki_keywords":
                            keywords = prop_val
                    elif item.get('type') == 'pin':

                        pin_type = item['content'][0]
                        pin_name = ""
                        pin_num = ""
                        for p in item['content']:
                            if isinstance(p, dict):
                                if p.get('type') == 'name':
                                    pin_name = p['content'][0]
                                elif p.get('type') == 'number':
                                    pin_num = p['content'][0]
                        pins.append({
                            "name": pin_name,
                            "number": pin_num,
                            "electrical_type": pin_type
                        })
                    elif 'content' in item:
                        process_content(item['content'])

        process_content(sym['content'])

        extracted.append({
            "exact_name": name,
            "library": None,
            "description": description,
            "keywords": keywords,
            "pins": pins
        })

    return extracted


def main():
    json_lib_path = 'assets/librarys/json_lib'
    if not os.path.exists(json_lib_path):
        print(f"Path not found: {json_lib_path}")
        return

    kicad_index_path = 'assets/kicad_library_index.json'
    lib_lookup = _load_kicad_library_lookup(kicad_index_path)
    if lib_lookup:
        print(f"Loaded {len(lib_lookup)} library mappings from {kicad_index_path}")
    else:
        print(f"WARNING: KiCad index not found at {kicad_index_path} — all libraries will be 'UNKNOWN'")

    all_symbols = []
    print(f"Indexing JSON libraries in: {json_lib_path}")

    files = [f for f in os.listdir(json_lib_path) if f.endswith('.json')]
    total = len(files)

    for i, file in enumerate(files):
        file_path = os.path.join(json_lib_path, file)
        try:
            symbols = parse_json_symbol(file_path)
            all_symbols.extend(symbols)
            if i % 1000 == 0:
                print(f"  Processed {i}/{total} files...")
        except Exception as e:
            # print(f"  Error parsing {file}: {e}")
            pass

    resolved = 0
    unresolved = 0
    for sym in all_symbols:
        real_lib = lib_lookup.get(sym["exact_name"])
        if real_lib:
            sym["library"] = real_lib
            resolved += 1
        else:
            sym["library"] = "UNKNOWN"
            unresolved += 1

    output_path = 'assets/json_library_index.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(all_symbols, f, indent=2)

    print(f"\nSuccessfully indexed {len(all_symbols)} symbols to {output_path}")
    print(f"  Library resolved : {resolved}")
    print(f"  Library unknown  : {unresolved}")


if __name__ == "__main__":
    main()
