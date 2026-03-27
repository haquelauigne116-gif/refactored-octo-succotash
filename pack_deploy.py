import os
import zipfile
import shutil

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_ZIP = os.path.join(ROOT_DIR, "learn_ai_deploy.zip")

EXCLUDE_DIRS = {
    "venv", ".venv", ".git", "__pycache__", ".vscode", ".idea",
    ".gemini", "drafts", ".pytest_cache"
}

# The files inside data/ that we MUST KEEP
DATA_KEEP_FILES = {"system_prompt.md"}

def should_skip(filepath):
    rel_path = os.path.relpath(filepath, ROOT_DIR).replace("\\", "/")
    
    # Exclude root zip
    if rel_path == "learn_ai_deploy.zip" or rel_path.endswith(".zip"):
        return True
        
    # Exclude certain extensions globally
    if rel_path.endswith(".pyc") or rel_path.endswith(".log"):
        return True
        
    # Complex rules for data directory
    if rel_path.startswith("data/"):
        filename = os.path.basename(rel_path)
        if filename not in DATA_KEEP_FILES and not rel_path.endswith(".keep"):
            return True

    return False

def pack():
    if os.path.exists(OUTPUT_ZIP):
        os.remove(OUTPUT_ZIP)
        
    print(f"Creating clean deployment package: {OUTPUT_ZIP}")
    
    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        # Walk through the directory
        for root, dirs, files in os.walk(ROOT_DIR):
            # Skip excluded dirs
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            
            # Ensure empty directories are added if needed
            rel_root = os.path.relpath(root, ROOT_DIR)
            if rel_root in ("data", "data\\session", "data\\memory", "data\\knowledge_base"):
                zf.writestr(rel_root.replace("\\", "/") + "/.keep", "")
            
            for f in files:
                abs_path = os.path.join(root, f)
                if not should_skip(abs_path):
                    rel_path = os.path.relpath(abs_path, ROOT_DIR)
                    zf.write(abs_path, rel_path)
                    print(f"  Added: {rel_path}")
                    
    print("\n[SUCCESS] 'learn_ai_deploy.zip' has been created!")
    print("This zip file contains a clean project excluding databases, memory files, and temporary sessions, while retaining your AI config keys.")

if __name__ == "__main__":
    pack()
