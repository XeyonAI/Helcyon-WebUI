import os

chats_dir = "chats"

# Get list of all your characters
characters = []
for f in os.listdir("characters"):
    if f.endswith(".json"):
        characters.append(f.replace(".json", ""))

print("=" * 60)
print("🔧 FIXING OLD CHAT FILENAMES")
print("=" * 60)
print(f"\nFound {len(characters)} characters: {', '.join(characters)}\n")

fixed_count = 0
skipped_count = 0

for filename in sorted(os.listdir(chats_dir)):
    if not filename.endswith(".txt"):
        continue
    
    # Check if already has character prefix
    has_prefix = any(filename.startswith(char + " - ") for char in characters)
    
    if has_prefix:
        print(f"✅ Already correct: {filename}")
        skipped_count += 1
        continue
    
    # Needs fixing
    print(f"\n⚠️  Missing prefix: {filename}")
    print(f"    Which character? Options: {', '.join(characters)}")
    
    choice = input("    Enter character name (or 's' to skip): ").strip()
    
    if choice.lower() == 's':
        print(f"    ⏭️  Skipped\n")
        skipped_count += 1
        continue
    
    if choice not in characters:
        print(f"    ❌ Invalid character name, skipping\n")
        skipped_count += 1
        continue
    
    # Rename the file
    new_filename = f"{choice} - {filename}"
    old_path = os.path.join(chats_dir, filename)
    new_path = os.path.join(chats_dir, new_filename)
    
    try:
        os.rename(old_path, new_path)
        print(f"    ✅ Renamed to: {new_filename}\n")
        fixed_count += 1
    except Exception as e:
        print(f"    ❌ Error: {e}\n")
        skipped_count += 1

print("=" * 60)
print(f"✅ Fixed: {fixed_count} chats")
print(f"⏭️  Skipped: {skipped_count} chats")
print("=" * 60)