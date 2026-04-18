#!/usr/bin/env python3
"""
Example: Search for jobs and scrape job details
"""
import asyncio
import sys
sys.path.append("..")  # Add parent directory to path for imports
from linkedin_scraper.scrapers.job_search import JobSearchScraper
from linkedin_scraper.scrapers.job import JobScraper
from linkedin_scraper.core.browser import BrowserManager
from gsheet_export import export_to_gsheet


JOB_SHEET_HEADERS = [
    "linkedin_url",
    "job_title",
    "company",
    "company_linkedin_url",
    "location",
    "workplace_type",
    "posted_date",
    "applicant_count",
    "application_status",
    "is_accepting_applications",
    "job_description",
    "benefits",
]


async def main():
    """Search for jobs and scrape details"""
    
    job_results = []
    async with BrowserManager(headless=True) as browser:
        await browser.load_session("../linkedin_session.json")
        print("✓ Session loaded")
        
        # Search for jobs
        search_scraper = JobSearchScraper(browser.page)
        print("🔍 Searching for jobs...")
        # job_urls = await search_scraper.search(
        #     keywords="data engineer",
        #     # location="Toronto",
        #     limit=1
        # )
        job_urls = ["https://www.linkedin.com/jobs/view/4402033871/"]

        print(f"\n✓ Found {len(job_urls)} jobs")
        for url in job_urls:
            print(f"  - {url}")
        
        # Scrape first job details if any found
        if job_urls:
            print(f"\n📄 Scraping first job details...")
            job_scraper = JobScraper(browser.page)
            for job_url in job_urls:
                job = await job_scraper.scrape(job_url)

                if job.is_accepting_applications is False:
                    print(
                        "Skip export (closed): "
                        f"{job.job_title} - {job.application_status}"
                    )
                    continue

                job_results.append(job.to_dict())

                print("\n" + "="*60)
                print(f"Title: {job.job_title}")
                print(f"Company: {job.company}")
                print(f"Location: {job.location}")
                print(f"Workplace type: {job.workplace_type}")
                print(f"Posted: {job.posted_date}")
                print(f"Applicants: {job.applicant_count}")
                print(f"Application status: {job.application_status}")
                print(f"Description: {job.job_description[:200]}..." if job.job_description else "Description: N/A")
                print("="*60)

    print("\n✓ Done!")

    if job_results:
        SPREADSHEET_ID = "1wJ_SxsMP8bQQdDnpmsDAtYn1WwUDgB5vmQ5spGYjlqk"
        WORKSHEET_NAME = "scraped jobs"
        CREDS_JSON = "../esg01-483205-3623ae733c6e.json"
        result = export_to_gsheet(
            job_results,
            SPREADSHEET_ID,
            WORKSHEET_NAME,
            CREDS_JSON,
            headers=JOB_SHEET_HEADERS,
            unique_key="linkedin_url",
        )
        print(
            f"✓ Google Sheet sync complete: inserted={result['inserted']}, "
            f"skipped={result['skipped']}"
        )

if __name__ == "__main__":
    asyncio.run(main())
