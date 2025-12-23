# Zotero Abstract Fetcher Tools

Two Python scripts to batch-fill missing abstracts in your Zotero library.

## Overview

| Script | Source | Best For |
|--------|--------|----------|
| `zotero_abstract_fetcher.py` | OpenAlex API | Automated batch processing via DOI lookup |
| `zotero_pdf_abstract_extractor.py` | Your PDF files | Items not in OpenAlex, executive summaries, reports |

**Both scripts support two modes:**
- **Interactive Mode**: Review and approve each abstract before updating
- **File-Based Mode**: Generate a log file, review offline, then batch update selected items

**Recommended workflow:**
1. Run the OpenAlex fetcher first (automated, handles most peer-reviewed items)
2. Run the PDF extractor for remaining items (handles reports, grey literature, etc.)

---

## Setup

### 1. Install Dependencies

```bash
pip install pyzotero requests PyMuPDF
```

### 2. Get Your Zotero Credentials

1. **Library ID**: Go to https://www.zotero.org/settings/keys
   - Look for "Your userID for use in API calls" (it's a number like `12345678`)

2. **API Key**: Go to https://www.zotero.org/settings/keys/new
   - Description: "Abstract Fetcher" (or whatever you like)
   - Check "Allow library access"
   - Check "Allow write access"
   - Click "Save Key" and copy the generated key

### 3. Configure the Scripts

Create a file PRIVATE_KEYS.py with these items configured

```python
ZOTERO_LIBRARY_ID = "library ID"      # Your numeric user ID
ZOTERO_LIBRARY_TYPE = "user"          # Keep as "user" for personal library
ZOTERO_API_KEY = "api key"            # Your API key
```

**Optional but recommended**: Add your email for faster OpenAlex API access:
```python
OPENALEX_EMAIL = "your.email@university.edu"
```

---

## Usage

### Script 1: OpenAlex Fetcher

This script queries the free OpenAlex scholarly database using DOIs.

#### Basic Usage

```bash
# Preview what would be updated (ALWAYS do this first)
python zotero_abstract_fetcher.py --dry-run

# Process only first 20 items (good for testing)
python zotero_abstract_fetcher.py --limit 20

# Process all items
python zotero_abstract_fetcher.py

# Process only a specific collection
python zotero_abstract_fetcher.py --collection ABC123XY
```

#### Selective Update Workflow (Recommended)

For better control over which abstracts to update:

```bash
# Step 1: Generate a log file with all potential updates
python zotero_abstract_fetcher.py --dry-run --verbose

# Step 2: Copy the generated log file to abstract_updates.txt
# (The log file will be named like: zotero_fetcher_20231215_143022.log)
copy zotero_fetcher_20231215_143022.log abstract_updates.txt

# Step 3: Edit abstract_updates.txt - delete entries you DON'T want to update

# Step 4: Preview selective updates
python zotero_abstract_fetcher.py --dry-run

# Step 5: Apply selective updates
python zotero_abstract_fetcher.py

# Step 6: Delete abstract_updates.txt to return to normal mode
del abstract_updates.txt
```

**Finding collection keys**: In Zotero, right-click a collection → "Copy Collection Link".
The key is the alphanumeric string at the end of the URL.

### Script 2: PDF Extractor

This script extracts abstracts from your PDF attachments. Supports both interactive and file-based modes.

#### Interactive Mode

Shows you each extracted abstract and asks for approval:

```bash
# Preview what PDFs would be processed
python zotero_pdf_abstract_extractor.py --dry-run

# Process first 10 items interactively
python zotero_pdf_abstract_extractor.py --limit 10

# Process all items with PDFs
python zotero_pdf_abstract_extractor.py
```

**Interactive options when reviewing each extraction:**
- `y` (yes): Accept and update Zotero
- `n` (no): Skip this item
- `e` (edit): Manually enter/correct the abstract
- `q` (quit): Stop processing and exit

#### Selective Update Workflow (File-Based)

For batch review and selective updates:

```bash
# Step 1: Generate a log file with all PDF extractions
python zotero_pdf_abstract_extractor.py --dry-run --verbose

# Step 2: Copy the generated log file to abstract_updates.txt
# (The log file will be named like: zotero_pdf_extractor_20231215_143022.log)
copy zotero_pdf_extractor_20231215_143022.log abstract_updates.txt

# Step 3: Edit abstract_updates.txt - delete entries you DON'T want to update
# You can also edit the abstract text directly in the file

# Step 4: Preview selective updates
python zotero_pdf_abstract_extractor.py --dry-run

# Step 5: Apply selective updates
python zotero_pdf_abstract_extractor.py

# Step 6: Delete abstract_updates.txt to return to normal mode
del abstract_updates.txt
```

**Note:** The `abstract_updates.txt` file format is compatible between both scripts, so you can combine abstracts from OpenAlex and PDF extraction in the same file!

---

## What These Scripts Do

### OpenAlex Fetcher
1. Retrieves all items from your Zotero library
2. Filters to items missing abstracts that have DOIs
3. Queries OpenAlex API for each DOI
4. Updates Zotero with found abstracts

### PDF Extractor
1. Finds items missing abstracts with PDF attachments
2. Downloads each PDF temporarily
3. Extracts text from first 1-2 pages
4. Uses pattern matching to locate abstract/summary sections (including Executive Summary and Management Summary)
5. **Interactive mode**: Shows you the extracted text for approval
6. **Verbose mode**: Logs all extractions to a file for batch review
7. Updates Zotero based on your selections

---

## Selective Update File Format

Both scripts use the same `abstract_updates.txt` file format, making them interoperable.

### File Structure

The file contains entries in this format (with optional logging prefix):

```
Processing [1/10] Smith et al. 2020
  Title: The full title of the paper
  DOI: 10.1234/example.doi
  Abstract: The abstract text goes here...
```

### Workflow Tips

1. **Mix and match sources**: You can combine abstracts from both OpenAlex and PDF extraction in the same `abstract_updates.txt` file

2. **Edit abstracts inline**: You can modify the abstract text directly in the file before applying updates

3. **Remove unwanted entries**: Delete entire entries (all 4 lines) to exclude them from updates

4. **Verify DOIs**: The scripts match items by DOI first, then fall back to title matching if DOI lookup fails

5. **Log file compatibility**: Both timestamped log files and manually edited files work - the scripts automatically strip logging prefixes

### Example Workflow

```bash
# Get abstracts from OpenAlex
python zotero_abstract_fetcher.py --dry-run --verbose
copy zotero_fetcher_20231215_143022.log abstracts_openalex.txt

# Get abstracts from PDFs
python zotero_pdf_abstract_extractor.py --dry-run --verbose
copy zotero_pdf_extractor_20231215_150000.log abstracts_pdf.txt

# Combine both files into abstract_updates.txt
# Review and edit to keep only desired entries

# Apply all selected updates at once
python zotero_abstract_fetcher.py  # or use either script
```

---

## Troubleshooting

### "pyzotero not installed"
```bash
pip install pyzotero
```

### "Permission denied" or API errors
- Verify your API key has write access
- Check that your Library ID is correct (numeric only)

### "Not found in OpenAlex"
- The paper may not be indexed in OpenAlex (especially older papers, non-English, or grey literature)
- The DOI might be malformed—check it in Zotero
- Use the PDF extractor as a fallback

### PDF extraction finds wrong text
- Use the `e` (edit) option to manually correct it
- Some PDFs have unusual layouts that confuse the extractor
- Scanned PDFs without OCR won't work

### Rate limiting
- OpenAlex: Very generous limits. Adding your email gives you faster access.
- Zotero API: Default limits are sufficient for these scripts

---

## Data Sources

### OpenAlex
- Free, open scholarly database
- ~250 million works indexed
- No registration required
- Good coverage of recent peer-reviewed literature
- https://openalex.org/

### Your PDFs
- Extracts directly from files in your Zotero storage
- Works offline (after initial Zotero sync)
- Useful for papers not in OpenAlex

---

## Limitations

- **DOI required** for OpenAlex lookup (most peer-reviewed papers have DOIs)
- **PDF quality matters** for extraction (scanned images without OCR won't work)
- **Not perfect** - always review results, especially from PDF extraction
- **One-way sync** - these scripts update Zotero, not the reverse

---

## Support

These scripts were created for academic literature review workflows. If you encounter issues:
1. Run with `--dry-run` first to preview changes
2. Start with `--limit 10` to test on a small batch
3. Check that your credentials are correct
4. Verify the DOI format in Zotero for failed items
