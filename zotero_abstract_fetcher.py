#!/usr/bin/env python3
"""
Zotero Abstract Fetcher
=======================
Fetches missing abstracts from OpenAlex and updates your Zotero library.

Requirements:
    pip install pyzotero requests

Setup:
    1. Get your Zotero User ID from: https://www.zotero.org/settings/keys
       (Look for "Your userID for use in API calls")
    2. Create an API key at: https://www.zotero.org/settings/keys/new
       (Enable "Allow library access" and "Allow write access")
    3. Update the configuration below with your credentials

Usage:
    python zotero_abstract_fetcher.py [--dry-run] [--limit N] [--collection COLLECTION_KEY]

Options:
    --dry-run       Preview changes without modifying Zotero (recommended first run)
    --limit N       Process only the first N items missing abstracts
    --collection    Only process items in a specific collection (use collection key)
    --verbose       Log detailed progress to timestamped file (e.g., zotero_fetcher_20231215_143022.log)

Selective Update Workflow:
    1. Run with --dry-run --verbose to generate a log file
    2. Copy the log file to abstract_updates.txt
    3. Edit abstract_updates.txt to remove entries you don't want to update
    4. Run the script again (with or without --dry-run)
    5. The script will only update items remaining in abstract_updates.txt
    6. Delete abstract_updates.txt to return to normal OpenAlex search mode

Author: Created with Claude for academic literature review workflows
"""

import argparse
import time
import re
import sys
import logging
import os
from datetime import datetime
from typing import Optional, List, Dict

try:
    from pyzotero import zotero
except ImportError:
    print("Error: pyzotero not installed. Run: pip install pyzotero")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("Error: requests not installed. Run: pip install requests")
    sys.exit(1)


# =============================================================================
# CONFIGURATION - update in PRIVATE_KEYS.py file and DO NOT check into github
# =============================================================================
from PRIVATE_KEYS import *


# =============================================================================
# END CONFIGURATION
# =============================================================================


class ZoteroAbstractFetcher:
    """Fetches missing abstracts from OpenAlex and updates Zotero."""
    
    def __init__(self, library_id: str, library_type: str, api_key: str,
                 email: Optional[str] = None, verbose: bool = False):
        self.zot = zotero.Zotero(library_id, library_type, api_key)
        self.email = email
        self.verbose = verbose
        self.stats = {
            "total_checked": 0,
            "missing_abstract": 0,
            "missing_abstract_with_doi": 0,
            "abstracts_found": 0,
            "abstracts_updated": 0,
            "errors": 0
        }

        # Set up logging to file if verbose mode is enabled
        self.logger = None
        if verbose:
            # Create log filename with timestamp
            log_filename = f"zotero_fetcher_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

            # Configure logger
            self.logger = logging.getLogger('ZoteroAbstractFetcher')
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

    def get_items_missing_abstracts(self, collection_key: Optional[str] = None,
                                     limit: Optional[int] = None) -> list:
        """Retrieve all items from Zotero that are missing abstracts."""
        self.log("Fetching items missing abstracts from Zotero library...", always=True)

        # Get all items (need to use everything() to handle pagination)
        if collection_key:
            items = self.zot.everything(self.zot.collection_items(collection_key))
        else:
            items = self.zot.everything(self.zot.items())

        missing_abstracts = []

        for item in items:
            data = item.get("data", {})
            item_type = data.get("itemType", "")

            # Skip attachments, notes, and other non-citation types
            if item_type in ["attachment", "note", "annotation"]:
                continue

            self.stats["total_checked"] += 1

            # Check if abstract is missing or empty
            abstract = data.get("abstractNote", "").strip()
            if abstract:
                continue  # Already has an abstract

            self.stats["missing_abstract"] += 1

            # Check if DOI exists
            doi = data.get("DOI", "").strip()
            if not doi:
                # Try to extract DOI from URL field
                url = data.get("url", "")
                doi_match = re.search(r'10\.\d{4,}/[^\s]+', url)
                if doi_match:
                    doi = doi_match.group(0)

            if doi:
                self.stats["missing_abstract_with_doi"] += 1
                missing_abstracts.append({
                    "key": item["key"],
                    "version": item["version"],
                    "doi": self.clean_doi(doi),
                    "title": data.get("title", "Unknown"),
                    "data": data
                })

        self.log(f"Found {len(missing_abstracts)} items missing abstracts with DOIs", always=True)

        if limit and len(missing_abstracts) > limit:
            self.log(f"Limiting to first {limit} items", always=True)
            missing_abstracts = missing_abstracts[:limit]

        return missing_abstracts
    
    def clean_doi(self, doi: str) -> str:
        """Clean DOI string to standard format."""
        # Remove common prefixes
        doi = re.sub(r'^https?://(dx\.)?doi\.org/', '', doi)
        doi = re.sub(r'^doi:', '', doi, flags=re.IGNORECASE)
        # Remove trailing punctuation that might have been captured
        doi = doi.rstrip('.,;:')
        return doi.strip()

    def fetch_abstract_from_openalex(self, doi: str) -> Optional[str]:
        """Query OpenAlex API for abstract using DOI."""
        # OpenAlex uses the DOI as a work ID with doi: prefix
        url = f"https://api.openalex.org/works/doi:{doi}"
        
        headers = {"Accept": "application/json"}
        if self.email:
            headers["User-Agent"] = f"ZoteroAbstractFetcher/1.0 (mailto:{self.email})"
        
        try:
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 404:
                self.log(f"  Not found in OpenAlex: {doi}")
                return None
            
            response.raise_for_status()
            data = response.json()
            
            # OpenAlex stores abstract as "abstract_inverted_index"
            # which needs to be reconstructed
            abstract_index = data.get("abstract_inverted_index")
            
            if abstract_index:
                abstract = self.reconstruct_abstract(abstract_index)
                if abstract:
                    self.stats["abstracts_found"] += 1
                    return abstract
            
            self.log(f"  No abstract available in OpenAlex for: {doi}")
            return None
            
        except requests.exceptions.RequestException as e:
            self.log(f"  Error fetching from OpenAlex: {e}")
            self.stats["errors"] += 1
            return None
    
    def reconstruct_abstract(self, inverted_index: dict) -> str:
        """
        Reconstruct abstract from OpenAlex inverted index format.

        OpenAlex stores abstracts as {word: [positions]} to save space.
        We need to reconstruct the original text.
        """
        if not inverted_index:
            return ""

        # Create a list of (position, word) tuples
        position_words = []
        for word, positions in inverted_index.items():
            for pos in positions:
                position_words.append((pos, word))

        # Sort by position and join words
        position_words.sort(key=lambda x: x[0])
        abstract = " ".join(word for _, word in position_words)

        return abstract

    def parse_abstract_updates_file(self, filename: str = "abstract_updates.txt") -> Optional[List[Dict]]:
        """
        Parse abstract_updates.txt file to get list of items to update.

        File format (from log file with or without logging prefix):
            2025-12-22 16:11:52,549 - INFO - Processing [1/5] Smith et al. 2020
            2025-12-22 16:11:52,549 - INFO -   Title: Article Title
            2025-12-22 16:11:52,549 - INFO -   DOI: 10.1234/example
            2025-12-22 16:11:52,549 - INFO -   [DRY RUN] Would update abstract for Smith et al. 2020 (500 chars)
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

            if title_safe != title_short:
                print(f"\n[{i}/{len(updates)}] Title contains non-ASCII characters:")
                print(f"  Display version: {title_safe}")
            else:
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
                    self.stats["errors"] += 1
                    continue

                # Update the abstract
                self.update_zotero_abstract(
                    matching_item["key"],
                    matching_item["version"],
                    abstract,
                    matching_item["data"],
                    dry_run
                )

            except Exception as e:
                self.log(f"  Error processing item: {e}")
                print(f"  Error processing item: {e}")
                self.stats["errors"] += 1

            # Be polite - small delay between requests
            time.sleep(0.2)

    def update_zotero_abstract(self, item_key: str, item_version: int,
                               abstract: str, item_data: dict, dry_run: bool = False) -> bool:
        """Update a Zotero item with the fetched abstract."""
        citation = self.format_citation(item_data)

        if dry_run:
            self.log(f"  [DRY RUN] Would update abstract for {citation} ({len(abstract)} chars)")
            self.log(f"  Abstract: {abstract}")
            self.stats["abstracts_updated"] += 1
            return True

        try:
            # Update the abstract field
            item_data["abstractNote"] = abstract

            # Prepare the update payload
            # Note: pyzotero expects the data fields to be at the top level with the key and version
            update_payload = item_data.copy()
            update_payload["key"] = item_key
            update_payload["version"] = item_version

            # Push update to Zotero
            self.zot.update_item(update_payload)
            self.stats["abstracts_updated"] += 1
            self.log(f"  Updated abstract for {citation} ({len(abstract)} chars)")
            self.log(f"  Abstract: {abstract}")
            return True

        except Exception as e:
            self.log(f"  Error updating Zotero abstract for {citation}: {e}")
            self.stats["errors"] += 1
            return False
    
    def run(self, collection_key: Optional[str] = None,
            limit: Optional[int] = None, dry_run: bool = False):
        """Main execution method."""

        if dry_run:
            print("\n" + "="*60)
            print("DRY RUN MODE - No changes will be made to your Zotero library")
            print("="*60 + "\n")

        # Check if abstract_updates.txt exists
        updates_from_file = self.parse_abstract_updates_file()

        if updates_from_file:
            # Use the abstract_updates.txt file instead of querying OpenAlex
            print("\nUsing abstract_updates.txt for selective updates")
            print("="*60)
            self.process_from_updates_file(updates_from_file, dry_run)
            self.print_summary(dry_run)
            return

        # Fetch items missing abstracts
        abstract_items = self.get_items_missing_abstracts(collection_key, limit)

        if abstract_items:
            print(f"\nProcessing {len(abstract_items)} items missing abstracts...")
            print("-" * 60)

            for i, item in enumerate(abstract_items, 1):
                title_short = item["title"][:50] + "..." if len(item["title"]) > 50 else item["title"]
                citation = self.format_citation(item["data"])

                # Log citation and full title to file
                self.log(f"Processing [{i}/{len(abstract_items)}] {citation}")
                self.log(f"  Title: {item['title']}")
                self.log(f"  DOI: {item['doi']}")

                # Handle Unicode characters that might not be printable in Windows console
                title_safe = title_short.encode('ascii', errors='replace').decode('ascii')

                # Warn if title contains non-ASCII characters
                if title_safe != title_short:
                    print(f"\n[{i}/{len(abstract_items)}] Title contains non-ASCII characters:")
                    print(f"  Original (copy this): {repr(title_short)}")
                    print(f"  Display version: {title_safe}")
                else:
                    print(f"\n[{i}/{len(abstract_items)}] {title_safe}")

                # Fetch abstract from OpenAlex
                abstract = self.fetch_abstract_from_openalex(item["doi"])

                if abstract:
                    self.update_zotero_abstract(
                        item["key"],
                        item["version"],
                        abstract,
                        item["data"],
                        dry_run
                    )

                # Be polite to the API - small delay between requests
                # OpenAlex allows 10 requests/second for polite pool, 1/second otherwise
                time.sleep(0.2 if self.email else 1.0)
        else:
            print("\nNo items found missing abstracts.")

        # Print summary
        self.print_summary(dry_run)
    
    def print_summary(self, dry_run: bool):
        """Print execution summary."""
        summary_lines = []
        summary_lines.append("=" * 60)
        summary_lines.append("SUMMARY")
        summary_lines.append("=" * 60)
        summary_lines.append(f"Total items checked:                {self.stats['total_checked']}")
        summary_lines.append(f"Items missing abstracts:            {self.stats['missing_abstract']}")
        summary_lines.append(f"Items missing abstracts (with DOI): {self.stats['missing_abstract_with_doi']}")
        summary_lines.append(f"Abstracts found in OpenAlex:        {self.stats['abstracts_found']}")
        action_abstract = "Would update abstracts:" if dry_run else "Abstracts updated:"
        summary_lines.append(f"{action_abstract}             {self.stats['abstracts_updated']}")
        if self.stats['errors']:
            summary_lines.append(f"Errors encountered:                 {self.stats['errors']}")
        summary_lines.append("=" * 60)

        # Print to console
        print("\n" + "\n".join(summary_lines))

        # Log to file
        for line in summary_lines:
            self.log(line)

        if dry_run and self.stats['abstracts_updated'] > 0:
            message = "\nTo apply these changes, run again without --dry-run"
            print(message)
            self.log(message)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch missing abstracts from OpenAlex and update Zotero library",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --dry-run                    Preview all changes without modifying Zotero
  %(prog)s --dry-run --limit 10         Preview changes for first 10 items
  %(prog)s --limit 50                   Update first 50 items missing abstracts
  %(prog)s --collection ABC123XY        Only process items in specific collection

First run recommendation:
  %(prog)s --dry-run --verbose          See detailed preview of all potential changes

Selective Update Workflow:
  1. %(prog)s --dry-run --verbose       Generate log file with all potential updates
  2. Copy log file to abstract_updates.txt and edit to keep only desired updates
  3. %(prog)s --dry-run                 Preview selective updates from file
  4. %(prog)s                           Apply selective updates to Zotero
  5. Delete abstract_updates.txt to return to normal OpenAlex search mode

Note: If abstract_updates.txt exists, the script will use it instead of searching OpenAlex.
        """
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without modifying Zotero")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit number of items to process")
    parser.add_argument("--collection", type=str, default=None,
                        help="Only process items in this collection (use collection key)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show detailed progress")
    
    args = parser.parse_args()
    
    # Validate configuration
    if ZOTERO_LIBRARY_ID == "YOUR_LIBRARY_ID" or ZOTERO_API_KEY == "YOUR_API_KEY":
        print("Error: Please update the configuration at the top of this script")
        print("       with your Zotero Library ID and API key.")
        print("\nTo get these values:")
        print("  1. Library ID: https://www.zotero.org/settings/keys")
        print("     (Look for 'Your userID for use in API calls')")
        print("  2. API Key: https://www.zotero.org/settings/keys/new")
        print("     (Create a new key with library access and write permission)")
        sys.exit(1)
    
    # Run the fetcher
    fetcher = ZoteroAbstractFetcher(
        library_id=ZOTERO_LIBRARY_ID,
        library_type=ZOTERO_LIBRARY_TYPE,
        api_key=ZOTERO_API_KEY,
        email=OPENALEX_EMAIL,
        verbose=args.verbose
    )
    
    fetcher.run(
        collection_key=args.collection,
        limit=args.limit,
        dry_run=args.dry_run
    )


if __name__ == "__main__":
    main()
