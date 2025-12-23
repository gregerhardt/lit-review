#!/usr/bin/env python3
"""
Zotero PDF Abstract Extractor
=============================
Extracts abstracts from PDF attachments for Zotero items missing abstracts.

This is a SUPPLEMENTARY script to zotero_abstract_fetcher.py. Use this for items
where OpenAlex doesn't have the abstract but you have the PDF.

Requirements:
    pip install pyzotero PyMuPDF

Setup:
    Same configuration as zotero_abstract_fetcher.py - update credentials below.

Usage:
    python zotero_pdf_abstract_extractor.py [--dry-run] [--limit N] [--verbose]

How it works:
    1. Finds items missing abstracts that have PDF attachments
    2. Downloads/reads the first page of each PDF
    3. Uses heuristics to locate and extract the abstract section
    4. In verbose mode: logs all extractions to a file for review
    5. Without verbose mode: asks for interactive approval before updating

Selective Update Workflow:
    1. Run with --dry-run --verbose to generate a log file
    2. Copy the log file to abstract_updates.txt
    3. Edit abstract_updates.txt to remove entries you don't want to update
    4. Run the script again (with or without --dry-run)
    5. The script will only update items remaining in abstract_updates.txt
    6. Delete abstract_updates.txt to return to normal mode

Note: The abstract_updates.txt format is compatible with zotero_abstract_fetcher.py

Author: Created with Claude for academic literature review workflows
"""

import argparse
import re
import sys
import tempfile
import os
import logging
from datetime import datetime
from typing import Optional, Tuple, List, Dict

try:
    from pyzotero import zotero
except ImportError:
    print("Error: pyzotero not installed. Run: pip install pyzotero")
    sys.exit(1)

try:
    import fitz  # PyMuPDF
except ImportError:
    print("Error: PyMuPDF not installed. Run: pip install PyMuPDF")
    sys.exit(1)


# =============================================================================
# CONFIGURATION - update in PRIVATE_KEYS.py file and DO NOT check into github
# =============================================================================
from PRIVATE_KEYS import *

# =============================================================================
# END CONFIGURATION
# =============================================================================


class PDFAbstractExtractor:
    """Extracts abstracts from PDFs attached to Zotero items."""
    
    # Common patterns that indicate the start of an abstract
    ABSTRACT_START_PATTERNS = [
        r'\bAbstract\b[:\s]*',
        r'\bSummary\b[:\s]*',
        r'\bExecutive\s+Summary\b[:\s]*',
        r'\bManagement\s+Summary\b[:\s]*',
        r'\bSYNOPSIS\b[:\s]*',
        r'\bABSTRACT\b[:\s]*',
        r'\bSUMMARY\b[:\s]*',
        r'\bEXECUTIVE\s+SUMMARY\b[:\s]*',
        r'\bMANAGEMENT\s+SUMMARY\b[:\s]*',
    ]
    
    # Patterns that indicate the end of an abstract
    ABSTRACT_END_PATTERNS = [
        r'\b(Keywords?|Key\s*words?)\s*:',
        r'\b(Table\s+of\s+Contents?|TABLE\s+OF\s+CONTENTS?|Contents?)\s*$',
        r'\b(Introduction|INTRODUCTION)\b',
        r'\b(Background|BACKGROUND)\b',
        r'\b1\.\s*(Introduction|INTRODUCTION)',
        r'\bI\.\s*(Introduction|INTRODUCTION)',
        r'^\s*\d+\.\s+[A-Z]',  # Numbered section headers
        r'\.\s*\.\s*\.\s*\.',  # Dotted lines often used in TOC
    ]
    
    def __init__(self, library_id: str, library_type: str, api_key: str, verbose: bool = False):
        self.zot = zotero.Zotero(library_id, library_type, api_key)
        self.verbose = verbose
        self.stats = {
            "total_missing": 0,
            "has_pdf": 0,
            "abstract_extracted": 0,
            "user_approved": 0,
            "user_skipped": 0,
            "extraction_failed": 0
        }

        # Set up logging to file if verbose mode is enabled
        self.logger = None
        if verbose:
            # Create log filename with timestamp
            log_filename = f"zotero_pdf_extractor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

            # Configure logger
            self.logger = logging.getLogger('PDFAbstractExtractor')
            self.logger.setLevel(logging.DEBUG)

            # File handler for verbose output
            file_handler = logging.FileHandler(log_filename, encoding='utf-8')
            file_handler.setLevel(logging.DEBUG)
            file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)

            # Print log file location
            print(f"Verbose logging enabled: {log_filename}\n")

    def log(self, message: str, always: bool = False):
        """
        Log message to file in verbose mode, print to console if always=True.

        Args:
            message: The message to log
            always: If True, print to console regardless of verbose mode
        """
        # Log to file if verbose mode is enabled
        if self.verbose and self.logger:
            self.logger.info(message)

        # Print to console only for important messages (always=True)
        if always:
            print(message)

    def format_citation(self, item_data: dict) -> str:
        """
        Format an author-date citation from Zotero item data.

        Args:
            item_data: Zotero item data dictionary

        Returns:
            Formatted citation string (e.g., "Smith 2020" or "Smith et al. 2020")
        """
        # Extract author information
        creators = item_data.get("creators", [])
        authors = [c for c in creators if c.get("creatorType") == "author"]

        # Format author part
        if not authors:
            author_part = "Unknown"
        elif len(authors) == 1:
            author_part = authors[0].get("lastName", "Unknown")
        elif len(authors) == 2:
            author_part = f"{authors[0].get('lastName', 'Unknown')} & {authors[1].get('lastName', 'Unknown')}"
        else:
            author_part = f"{authors[0].get('lastName', 'Unknown')} et al."

        # Extract year from date field
        date = item_data.get("date", "")
        year_match = re.search(r'\b(19|20)\d{2}\b', date)
        year = year_match.group(0) if year_match else "n.d."

        return f"{author_part} {year}"

    def clean_doi(self, doi: str) -> str:
        """Clean DOI string to standard format."""
        # Remove common prefixes
        doi = re.sub(r'^https?://(dx\.)?doi\.org/', '', doi)
        doi = re.sub(r'^doi:', '', doi, flags=re.IGNORECASE)
        # Remove trailing punctuation that might have been captured
        doi = doi.rstrip('.,;:')
        return doi.strip()

    def parse_abstract_updates_file(self, filename: str = "abstract_updates.txt") -> Optional[List[Dict]]:
        """
        Parse abstract_updates.txt file to get list of items to update.

        File format (from log file with or without logging prefix):
            2025-12-22 16:11:52,549 - INFO - Processing [1/5] Smith et al. 2020
            2025-12-22 16:11:52,549 - INFO -   Title: Article Title
            2025-12-22 16:11:52,549 - INFO -   DOI: 10.1234/example
            2025-12-22 16:11:52,549 - INFO -   Abstract: The abstract text here...

        The logging prefix is automatically stripped if present.

        Returns:
            List of dicts with keys: citation, title, doi, abstract
            Returns None if file doesn't exist
        """
        if not os.path.exists(filename):
            return None

        self.log(f"Reading updates from {filename}...", always=True)

        updates = []
        current_item = {}

        try:
            with open(filename, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.rstrip('\n')

                    # Strip logging prefix if present (e.g., "2025-12-22 16:11:52,549 - INFO - ")
                    log_prefix_match = re.match(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - INFO - (.*)$', line)
                    if log_prefix_match:
                        line = log_prefix_match.group(1)

                    # Skip empty lines and separator lines
                    if not line or line.startswith('=') or line.startswith('-'):
                        continue

                    # Processing line: extract citation
                    if line.startswith('Processing ['):
                        # Save previous item if exists
                        if current_item and 'doi' in current_item and 'abstract' in current_item:
                            updates.append(current_item)

                        # Start new item
                        # Extract citation after the closing bracket
                        match = re.search(r'Processing \[\d+/\d+\] (.+)$', line)
                        if match:
                            current_item = {'citation': match.group(1)}

                    # Title line
                    elif line.strip().startswith('Title:'):
                        title = line.strip()[6:].strip()
                        if current_item is not None:
                            current_item['title'] = title

                    # DOI line
                    elif line.strip().startswith('DOI:'):
                        doi = line.strip()[4:].strip()
                        if current_item is not None:
                            current_item['doi'] = doi

                    # Abstract line
                    elif line.strip().startswith('Abstract:'):
                        abstract = line.strip()[9:].strip()
                        if current_item is not None:
                            current_item['abstract'] = abstract

                # Don't forget the last item
                if current_item and 'doi' in current_item and 'abstract' in current_item:
                    updates.append(current_item)

        except Exception as e:
            self.log(f"Error reading {filename}: {e}", always=True)
            return None

        self.log(f"Found {len(updates)} items in {filename}", always=True)
        return updates if updates else None

    def get_items_missing_abstracts_with_pdfs(self, limit: Optional[int] = None) -> list:
        """Find items missing abstracts that have PDF attachments."""
        print("Fetching items from Zotero library...")

        # Use everything() to get all items, not just the first page
        items = self.zot.everything(self.zot.items())
        
        # First pass: find items missing abstracts
        missing_abstracts = []
        item_keys_missing = set()
        
        for item in items:
            data = item.get("data", {})
            item_type = data.get("itemType", "")
            
            if item_type in ["attachment", "note", "annotation"]:
                continue
            
            abstract = data.get("abstractNote", "").strip()
            if not abstract:
                self.stats["total_missing"] += 1
                missing_abstracts.append(item)
                item_keys_missing.add(item["key"])
        
        # Second pass: find PDF attachments for these items
        items_with_pdfs = []
        
        for item in items:
            data = item.get("data", {})
            item_type = data.get("itemType", "")
            
            if item_type == "attachment":
                content_type = data.get("contentType", "")
                parent_key = data.get("parentItem", "")
                
                if "pdf" in content_type.lower() and parent_key in item_keys_missing:
                    # Find the parent item
                    for parent in missing_abstracts:
                        if parent["key"] == parent_key:
                            self.stats["has_pdf"] += 1
                            items_with_pdfs.append({
                                "parent": parent,
                                "attachment_key": item["key"],
                                "filename": data.get("filename", "unknown.pdf")
                            })
                            break
        
        print(f"Found {self.stats['total_missing']} items missing abstracts")
        print(f"Of those, {self.stats['has_pdf']} have PDF attachments")
        
        if limit and len(items_with_pdfs) > limit:
            print(f"Limiting to first {limit} items")
            items_with_pdfs = items_with_pdfs[:limit]
        
        return items_with_pdfs
    
    def download_pdf(self, attachment_key: str) -> Optional[str]:
        """Download PDF attachment to a temporary file."""
        try:
            # Get the file content
            content = self.zot.file(attachment_key)
            
            # Write to temporary file
            temp_file = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
            temp_file.write(content)
            temp_file.close()
            
            return temp_file.name
            
        except Exception as e:
            print(f"  Error downloading PDF: {e}")
            return None
    
    def extract_abstract_from_pdf(self, pdf_path: str) -> Optional[str]:
        """Extract abstract text from PDF using heuristics."""
        try:
            doc = fitz.open(pdf_path)
            
            # Usually abstract is on page 1, sometimes page 2
            text = ""
            for page_num in range(min(2, len(doc))):
                page = doc[page_num]
                text += page.get_text()
            
            doc.close()
            
            # Try to find abstract section
            abstract = self._find_abstract_in_text(text)
            
            return abstract
            
        except Exception as e:
            print(f"  Error reading PDF: {e}")
            return None
    
    def _find_abstract_in_text(self, text: str) -> Optional[str]:
        """Use heuristics to find and extract abstract from text."""
        
        # Try each start pattern
        for start_pattern in self.ABSTRACT_START_PATTERNS:
            match = re.search(start_pattern, text, re.IGNORECASE)
            if match:
                # Found start of abstract
                start_pos = match.end()
                
                # Look for end of abstract
                remaining_text = text[start_pos:]
                
                end_pos = len(remaining_text)
                for end_pattern in self.ABSTRACT_END_PATTERNS:
                    end_match = re.search(end_pattern, remaining_text, re.IGNORECASE | re.MULTILINE)
                    if end_match and end_match.start() < end_pos:
                        end_pos = end_match.start()
                
                abstract = remaining_text[:end_pos].strip()
                
                # Clean up the abstract
                abstract = self._clean_abstract(abstract)
                
                # Validate: abstracts are typically 100-500 words
                word_count = len(abstract.split())
                if 50 < word_count < 1000:
                    return abstract
        
        return None
    
    def _clean_abstract(self, text: str) -> str:
        """Clean extracted abstract text."""
        # Replace multiple whitespace with single space
        text = re.sub(r'\s+', ' ', text)
        # Remove page numbers and headers that might have been captured
        text = re.sub(r'\d+\s*$', '', text)
        # Remove common artifacts
        text = re.sub(r'^\s*[\d\-]+\s*', '', text)
        return text.strip()
    
    def process_from_updates_file(self, updates: List[Dict], dry_run: bool = False):
        """
        Process abstracts from the abstract_updates.txt file.

        Args:
            updates: List of dicts with keys: citation, title, doi, abstract
            dry_run: If True, preview changes without updating Zotero
        """
        print(f"\nProcessing {len(updates)} items from abstract_updates.txt...")
        print("-" * 60)

        # Fetch all items once and create a DOI lookup dictionary
        print("Fetching library items from Zotero...")
        all_items = self.zot.everything(self.zot.items())

        # Build a DOI-to-item mapping for faster lookup
        doi_to_item = {}
        for item in all_items:
            data = item.get("data", {})
            item_doi = data.get("DOI", "").strip()
            if item_doi:
                # Store both cleaned and original DOI as keys
                cleaned_doi = self.clean_doi(item_doi)
                doi_to_item[cleaned_doi] = item
                doi_to_item[item_doi] = item  # Also store original format

        self.log(f"Built DOI lookup with {len(doi_to_item)} entries from {len(all_items)} items")
        print(f"Found {len(all_items)} items in library")
        print("-" * 60)

        for i, update in enumerate(updates, 1):
            citation = update.get('citation', 'Unknown')
            title = update.get('title', 'Unknown')
            doi = update.get('doi', '')
            abstract = update.get('abstract', '')

            # Log to file
            self.log(f"Processing [{i}/{len(updates)}] {citation}")
            self.log(f"  Title: {title}")
            self.log(f"  DOI: {doi}")

            # Safe title for console
            title_short = title[:50] + "..." if len(title) > 50 else title
            title_safe = title_short.encode('ascii', errors='replace').decode('ascii')
            print(f"\n[{i}/{len(updates)}] {title_safe}")

            # Find the Zotero item by DOI using the lookup dictionary
            try:
                cleaned_doi = self.clean_doi(doi)
                matching_item = doi_to_item.get(cleaned_doi) or doi_to_item.get(doi)

                if not matching_item:
                    # Try to find by title as a fallback
                    self.log(f"  DOI lookup failed, attempting title match for: {title}")
                    for item in all_items:
                        item_title = item.get("data", {}).get("title", "")
                        if item_title.lower().strip() == title.lower().strip():
                            matching_item = item
                            self.log(f"  Found match by title")
                            break

                if not matching_item:
                    self.log(f"  WARNING: Could not find Zotero item with DOI: {doi} (cleaned: {cleaned_doi})")
                    print(f"  WARNING: Could not find item with DOI: {doi}")
                    self.stats["extraction_failed"] += 1
                    continue

                # Update the abstract
                if self.update_zotero_item(matching_item, abstract, dry_run):
                    self.stats["user_approved"] += 1
                else:
                    self.stats["extraction_failed"] += 1

            except Exception as e:
                self.log(f"  Error processing item: {e}")
                print(f"  Error processing item: {e}")
                self.stats["extraction_failed"] += 1

    def update_zotero_item(self, item: dict, abstract: str, dry_run: bool = False) -> bool:
        """Update Zotero item with the extracted abstract."""
        try:
            citation = self.format_citation(item["data"])

            if dry_run:
                self.log(f"  [DRY RUN] Would update abstract for {citation} ({len(abstract)} chars)")
                self.log(f"  Abstract: {abstract}")
                print(f"  [DRY RUN] Would update abstract ({len(abstract)} chars)")
                return True

            data = item["data"].copy()
            data["abstractNote"] = abstract

            # Note: pyzotero expects the data fields to be at the top level with the key and version
            update_payload = data.copy()
            update_payload["key"] = item["key"]
            update_payload["version"] = item["version"]

            self.zot.update_item(update_payload)
            self.log(f"  Updated abstract for {citation} ({len(abstract)} chars)")
            self.log(f"  Abstract: {abstract}")
            print(f"  Updated abstract ({len(abstract)} chars)")
            return True

        except Exception as e:
            print(f"  Error updating Zotero: {e}")
            self.log(f"  Error updating Zotero: {e}")
            return False

    def run(self, limit: Optional[int] = None, dry_run: bool = False):
        """Main execution method with interactive or file-based approval."""

        if dry_run:
            print("\n" + "="*60)
            print("DRY RUN MODE - No changes will be made")
            print("="*60 + "\n")

        # Check if abstract_updates.txt exists
        updates_from_file = self.parse_abstract_updates_file()

        if updates_from_file:
            # Use the abstract_updates.txt file instead of PDF extraction
            print("\nUsing abstract_updates.txt for selective updates")
            print("="*60)
            self.process_from_updates_file(updates_from_file, dry_run)
            self.print_summary()
            return

        items = self.get_items_missing_abstracts_with_pdfs(limit)
        
        if not items:
            print("\nNo items found with missing abstracts and PDF attachments.")
            return
        
        print(f"\nProcessing {len(items)} items...")
        print("-" * 60)
        
        for i, item_info in enumerate(items, 1):
            parent = item_info["parent"]
            data = parent["data"]
            title = data.get("title", "Unknown")
            doi = data.get("DOI", "")
            citation = self.format_citation(data)
            title_short = title[:60] + "..." if len(title) > 60 else title

            # Log to file
            self.log(f"Processing [{i}/{len(items)}] {citation}")
            self.log(f"  Title: {title}")
            self.log(f"  DOI: {doi}")
            self.log(f"  PDF: {item_info['filename']}")

            print(f"\n[{i}/{len(items)}] {title_short}")
            print(f"  PDF: {item_info['filename']}")

            # Download PDF
            pdf_path = self.download_pdf(item_info["attachment_key"])
            if not pdf_path:
                self.log("  Failed to download PDF")
                self.stats["extraction_failed"] += 1
                continue

            try:
                # Extract abstract
                abstract = self.extract_abstract_from_pdf(pdf_path)

                if not abstract:
                    print("  Could not find abstract in PDF")
                    self.log("  Could not find abstract in PDF")
                    self.stats["extraction_failed"] += 1
                    continue

                self.stats["abstract_extracted"] += 1

                # Log extracted abstract
                self.log(f"  Abstract: {abstract}")

                # In verbose mode, just log without interaction
                if self.verbose:
                    print(f"  Extracted abstract ({len(abstract)} chars) - logged to file")
                    if dry_run:
                        self.log(f"  [DRY RUN] Would update abstract for {citation}")
                    continue

                # Show extracted abstract and ask for approval (interactive mode only)
                print("\n  --- Extracted Abstract ---")
                # Word wrap for display
                words = abstract.split()
                line = "  "
                for word in words:
                    if len(line) + len(word) > 78:
                        print(line)
                        line = "  " + word
                    else:
                        line += " " + word if line.strip() else word
                if line.strip():
                    print(line)
                print("  --- End Abstract ---\n")

                if dry_run:
                    print("  [DRY RUN] Would ask for approval to update")
                    continue

                # Interactive approval
                while True:
                    response = input("  Update Zotero with this abstract? [y]es / [n]o / [e]dit / [q]uit: ").lower().strip()

                    if response in ['y', 'yes']:
                        if self.update_zotero_item(parent, abstract, dry_run=False):
                            print("  ✓ Updated successfully")
                            self.stats["user_approved"] += 1
                        break
                    elif response in ['n', 'no']:
                        print("  Skipped")
                        self.stats["user_skipped"] += 1
                        break
                    elif response in ['e', 'edit']:
                        print("  Enter corrected abstract (end with empty line):")
                        lines = []
                        while True:
                            line = input()
                            if not line:
                                break
                            lines.append(line)
                        if lines:
                            abstract = " ".join(lines)
                            if self.update_zotero_item(parent, abstract, dry_run=False):
                                print("  ✓ Updated with edited abstract")
                                self.stats["user_approved"] += 1
                        break
                    elif response in ['q', 'quit']:
                        print("\nQuitting...")
                        self.print_summary()
                        return
                    else:
                        print("  Please enter y, n, e, or q")
                        
            finally:
                # Clean up temp file
                if pdf_path and os.path.exists(pdf_path):
                    os.unlink(pdf_path)
        
        self.print_summary()
    
    def print_summary(self):
        """Print execution summary."""
        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)
        print(f"Items missing abstracts:     {self.stats['total_missing']}")
        print(f"Items with PDF attachments:  {self.stats['has_pdf']}")
        print(f"Abstracts extracted:         {self.stats['abstract_extracted']}")
        print(f"User approved updates:       {self.stats['user_approved']}")
        print(f"User skipped:                {self.stats['user_skipped']}")
        print(f"Extraction failed:           {self.stats['extraction_failed']}")
        print("="*60)


def main():
    parser = argparse.ArgumentParser(
        description="Extract abstracts from PDF attachments for Zotero items",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --dry-run --verbose    Generate log file with all extractions for review
  %(prog)s --limit 10             Process first 10 items interactively
  %(prog)s                        Process all items with interactive approval

Selective Update Workflow:
  1. %(prog)s --dry-run --verbose       Generate log file with all extractions
  2. Copy log file to abstract_updates.txt and edit to keep only desired updates
  3. %(prog)s --dry-run                 Preview selective updates from file
  4. %(prog)s                           Apply selective updates to Zotero
  5. Delete abstract_updates.txt to return to normal mode

Note: If abstract_updates.txt exists, the script will use it instead of extracting from PDFs.
The file format is compatible with zotero_abstract_fetcher.py
        """
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview extractions without modifying Zotero")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit number of items to process")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Log detailed progress to timestamped file")

    args = parser.parse_args()

    # Validate configuration
    if ZOTERO_LIBRARY_ID == "YOUR_LIBRARY_ID" or ZOTERO_API_KEY == "YOUR_API_KEY":
        print("Error: Please update the configuration at the top of this script")
        print("       with your Zotero Library ID and API key.")
        sys.exit(1)

    extractor = PDFAbstractExtractor(
        library_id=ZOTERO_LIBRARY_ID,
        library_type=ZOTERO_LIBRARY_TYPE,
        api_key=ZOTERO_API_KEY,
        verbose=args.verbose
    )

    extractor.run(limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
