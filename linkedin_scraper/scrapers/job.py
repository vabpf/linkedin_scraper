"""
Job scraper for LinkedIn.

Extracts job posting information from LinkedIn job pages.
"""
import logging
import re
from typing import Optional
from playwright.async_api import Page

from ..models.job import Job
from ..core.exceptions import ProfileNotFoundError
from ..callbacks import ProgressCallback, SilentCallback
from .base import BaseScraper

logger = logging.getLogger(__name__)

JOB_READY_SELECTOR = 'main, [class*="jobs-unified-top-card"], [class*="top-card"]'
JOB_TITLE_SELECTORS = [
    '[class*="jobs-unified-top-card"] p',
    '[class*="jobs-unified-top-card"] div',
    '[class*="jobs-unified-top-card"] span',
    'main h1',
    'h1',
]
JOB_TOP_CARD_SELECTORS = [
    '[class*="jobs-unified-top-card"]',
    '[class*="top-card"]',
    'main',
]
JOB_METADATA_SELECTORS = [
    '[class*="jobs-unified-top-card__primary-description"]',
    '[class*="jobs-unified-top-card__tertiary-description"]',
    'main [class*="top-card"]',
]
JOB_DESCRIPTION_SELECTORS = [
    '#job-details',
    '[class*="jobs-description"]',
    'article',
]

METADATA_SPLIT_PATTERN = re.compile(r'\s*[·•]\s*')
POSTED_DATE_PATTERN = re.compile(
    r'(\d+\s*(?:minute|hour|day|week|month|year)s?\s+ago|reposted)',
    re.IGNORECASE,
)
APPLICANT_PATTERN = re.compile(
    r'(applicant|applied|people\s+clicked)',
    re.IGNORECASE,
)
WORKPLACE_REMOTE_PATTERN = re.compile(r'\bremote\b', re.IGNORECASE)
WORKPLACE_HYBRID_PATTERN = re.compile(r'\bhybrid\b', re.IGNORECASE)
WORKPLACE_ONSITE_PATTERN = re.compile(r'\bon\s*-?\s*site\b', re.IGNORECASE)
APPLICATION_CLOSED_PATTERN = re.compile(
    r'(no\s+longer\s+accepting\s+applications|applications?\s+closed|position\s+closed|job\s+closed|expired)',
    re.IGNORECASE,
)
APPLICATION_OPEN_PATTERN = re.compile(
    r'(apply\s+now|easy\s+apply|actively\s+recruiting)',
    re.IGNORECASE,
)
DESCRIPTION_PREFIX_PATTERN = re.compile(
    r'^(?:about\s+the\s+job|job\s+summary)\s*[:\-]?\s*',
    re.IGNORECASE,
)
LOCATION_HINT_PATTERN = re.compile(
    r'(remote|hybrid|on-?site|united\s+states|canada|india|singapore)',
    re.IGNORECASE,
)

JOB_TITLE_EXCLUDE_PATTERN = re.compile(
    r'^(apply|save|share|follow|more options|easy apply)$|'
    r'(applicant|reposted|ago|hiring|promoted by|response insights)',
    re.IGNORECASE,
)


class JobScraper(BaseScraper):
    """
    Scraper for LinkedIn job postings.
    
    Example:
        async with BrowserManager() as browser:
            scraper = JobScraper(browser.page)
            job = await scraper.scrape("https://www.linkedin.com/jobs/view/123456/")
            print(job.to_json())
    """
    
    def __init__(self, page: Page, callback: Optional[ProgressCallback] = None):
        """
        Initialize job scraper.
        
        Args:
            page: Playwright page object
            callback: Optional progress callback
        """
        super().__init__(page, callback or SilentCallback())
    
    async def scrape(self, linkedin_url: str) -> Job:
        """
        Scrape a LinkedIn job posting.
        
        Args:
            linkedin_url: URL of the LinkedIn job posting
            
        Returns:
            Job object with scraped data
            
        Raises:
            ProfileNotFoundError: If job posting not found
        """
        logger.info(f"Starting job scraping: {linkedin_url}")
        await self.callback.on_start("Job", linkedin_url)
        
        # Navigate to job page
        await self.navigate_and_wait(
            linkedin_url,
            wait_until='domcontentloaded',
            ready_selector=JOB_READY_SELECTOR,
            ready_state='visible',
            ready_timeout=15000,
            settle_network=False,
        )
        await self.callback.on_progress("Navigated to job page", 10)
        
        # Check if page exists
        await self.check_rate_limit()
        
        # Extract job details
        job_title = await self._get_job_title()
        await self.callback.on_progress(f"Got job title: {job_title}", 20)
        
        company = await self._get_company()
        await self.callback.on_progress("Got company name", 30)
        
        location = await self._get_location()
        await self.callback.on_progress("Got location", 40)

        workplace_type = await self._get_workplace_type()
        await self.callback.on_progress("Got workplace type", 45)
        
        posted_date = await self._get_posted_date()
        await self.callback.on_progress("Got posted date", 50)
        
        applicant_count = await self._get_applicant_count()
        await self.callback.on_progress("Got applicant count", 60)

        application_status, is_accepting_applications = await self._get_application_status()
        await self.callback.on_progress("Got application status", 70)
        
        job_description = await self._get_description()
        await self.callback.on_progress("Got job description", 80)
        
        company_url = await self._get_company_url()
        await self.callback.on_progress("Got company URL", 90)
        
        # Create job object
        job = Job(
            linkedin_url=linkedin_url,
            job_title=job_title,
            company=company,
            company_linkedin_url=company_url,
            location=location,
            workplace_type=workplace_type,
            posted_date=posted_date,
            applicant_count=applicant_count,
            job_description=job_description,
            application_status=application_status,
            is_accepting_applications=is_accepting_applications,
        )
        
        await self.callback.on_progress("Scraping complete", 100)
        await self.callback.on_complete("Job", job)
        
        logger.info(f"Successfully scraped job: {job_title}")
        return job
    
    async def _get_job_title(self) -> Optional[str]:
        """Extract job title using semantic and heuristic selectors."""
        # Strategy 1: infer title from top-card region near metadata/company blocks.
        inferred_title = await self._infer_title_from_top_card()
        if inferred_title:
            return inferred_title

        # Strategy 2: scan broad selectors and return first likely job title.
        for selector in JOB_TITLE_SELECTORS:
            try:
                elements = await self.page.locator(selector).all()
                for element in elements:
                    text = (await element.inner_text()).strip()
                    if self._is_probable_job_title(text):
                        return text
            except:
                continue

        return None

    async def _get_application_status(self) -> tuple[Optional[str], Optional[bool]]:
        """Extract whether the job still accepts applications."""
        try:
            main_content = self.page.locator('main').first
            if await main_content.count() > 0:
                text_blocks = await main_content.locator('p, span, div').all()
                for block in text_blocks:
                    text = (await block.inner_text()).strip()
                    if not text or len(text) > 120:
                        continue

                    normalized = ' '.join(text.split())
                    if APPLICATION_CLOSED_PATTERN.search(normalized):
                        return normalized, False
        except:
            pass

        try:
            apply_button = self.page.locator(
                'a:has-text("Apply"), button:has-text("Apply"), a:has-text("Easy Apply"), button:has-text("Easy Apply")'
            ).first
            if await apply_button.count() > 0:
                label = (await apply_button.inner_text()).strip()
                if label and APPLICATION_OPEN_PATTERN.search(label):
                    return label, True
                if label:
                    return label, True
        except:
            pass

        return None, None

    def _is_probable_job_title(self, text: str) -> bool:
        """Heuristic filter to keep likely title candidates and drop UI/meta strings."""
        if not text:
            return False

        normalized = ' '.join(text.split())
        if len(normalized) < 4 or len(normalized) > 120:
            return False
        if '\n' in text:
            return False
        if 'http' in normalized.lower():
            return False
        if METADATA_SPLIT_PATTERN.search(normalized):
            return False
        if POSTED_DATE_PATTERN.search(normalized):
            return False
        if APPLICANT_PATTERN.search(normalized):
            return False
        if LOCATION_HINT_PATTERN.search(normalized) and ',' in normalized:
            return False
        if JOB_TITLE_EXCLUDE_PATTERN.search(normalized):
            return False

        words = normalized.split()
        alpha_words = [w for w in words if any(ch.isalpha() for ch in w)]
        return 1 <= len(alpha_words) <= 12

    async def _infer_title_from_top_card(self) -> Optional[str]:
        """Infer title from top-card by finding best text candidate around metadata line."""
        company = await self._get_company()

        for container_selector in JOB_TOP_CARD_SELECTORS:
            try:
                container = self.page.locator(container_selector).first
                if await container.count() == 0:
                    continue

                candidates = await container.locator('p, h1, h2, span, div').all()
                seen: set[str] = set()
                best: Optional[str] = None

                for candidate in candidates:
                    text = (await candidate.inner_text()).strip()
                    text = ' '.join(text.split())
                    if not text or text in seen:
                        continue
                    seen.add(text)

                    if company and text == company:
                        continue
                    if self._is_probable_job_title(text):
                        # Prefer text that looks like role naming over generic content.
                        if any(
                            token in text.lower()
                            for token in ('engineer', 'developer', 'manager', 'analyst', 'specialist', 'architect')
                        ):
                            return text
                        if best is None:
                            best = text

                if best:
                    return best
            except:
                continue

        return None

    async def _get_metadata_parts(self) -> list[str]:
        """Extract metadata fragments (location/date/applicants) using robust fallbacks."""
        for selector in JOB_METADATA_SELECTORS:
            try:
                container = self.page.locator(selector).first
                await container.wait_for(state='attached', timeout=3000)
                if await container.count() == 0:
                    continue

                text = await container.inner_text()
                if not text:
                    continue

                normalized = ' '.join(text.split())
                parts = [
                    part.strip()
                    for part in METADATA_SPLIT_PATTERN.split(normalized)
                    if part and part.strip()
                ]
                if parts:
                    return parts
            except:
                continue

        # Fallback for pages where top metadata uses fully rotated class names.
        try:
            main = self.page.locator('main').first
            if await main.count() > 0:
                text_nodes = main.locator('p, span')
                max_nodes = min(await text_nodes.count(), 300)

                for i in range(max_nodes):
                    text = await text_nodes.nth(i).inner_text()
                    if not text:
                        continue

                    normalized = ' '.join(text.split())
                    if '·' not in normalized and '•' not in normalized:
                        continue
                    if not (
                        POSTED_DATE_PATTERN.search(normalized)
                        or APPLICANT_PATTERN.search(normalized)
                    ):
                        continue

                    parts = [
                        part.strip()
                        for part in METADATA_SPLIT_PATTERN.split(normalized)
                        if part and part.strip()
                    ]
                    if parts:
                        return parts
        except:
            pass

        return []
    
    async def _get_company(self) -> Optional[str]:
        """Extract company name from company link."""
        try:
            # Find company links that have text (not just images)
            company_links = await self.page.locator('a[href*="/company/"]').all()
            for link in company_links:
                text = await link.inner_text()
                text = text.strip()
                # Skip empty or very short text (likely image-only links)
                if text and len(text) > 1 and not text.startswith('logo'):
                    return text
        except:
            pass
        return None
    
    async def _get_company_url(self) -> Optional[str]:
        """Extract company LinkedIn URL."""
        try:
            company_link = self.page.locator('a[href*="/company/"]').first
            if await company_link.count() > 0:
                href = await company_link.get_attribute('href')
                if href:
                    if '?' in href:
                        href = href.split('?')[0]
                    if not href.startswith('http'):
                        href = f"https://www.linkedin.com{href}"
                    return href
        except:
            pass
        return None
    
    async def _get_location(self) -> Optional[str]:
        """Extract job location from job details panel."""
        try:
            parts = await self._get_metadata_parts()
            for part in parts:
                if (
                    not POSTED_DATE_PATTERN.search(part)
                    and not APPLICANT_PATTERN.search(part)
                    and not self._normalize_workplace_type(part)
                ):
                    return part.strip().split('\n')[0].strip()
        except:
            pass
            
        try:
            job_panel = self.page.locator('h1').first.locator('xpath=ancestor::*[5]')
            if await job_panel.count() > 0:
                text_elements = await job_panel.locator('span, div').all()
                for elem in text_elements:
                    text = await elem.inner_text()
                    if text and (',' in text or 'Remote' in text or 'United States' in text):
                        text = text.strip()
                        # Avoid matching the job title if it contains a comma
                        title = await self._get_job_title()
                        if title and text == title:
                            continue
                        if self._normalize_workplace_type(text):
                            continue
                        if len(text) > 3 and len(text) < 100 and not text.startswith('$'):
                            return text
        except:
            pass
        return None

    def _normalize_workplace_type(self, text: str) -> Optional[str]:
        """Normalize workplace labels to one of: Remote, Hybrid, On-site."""
        if not text:
            return None
        normalized = ' '.join(text.split())

        if WORKPLACE_HYBRID_PATTERN.search(normalized):
            return "Hybrid"
        if WORKPLACE_REMOTE_PATTERN.search(normalized):
            return "Remote"
        if WORKPLACE_ONSITE_PATTERN.search(normalized):
            return "On-site"
        return None

    async def _get_workplace_type(self) -> Optional[str]:
        """Extract workplace type (Remote/Hybrid/On-site) from metadata and page text."""
        try:
            parts = await self._get_metadata_parts()
            for part in parts:
                workplace_type = self._normalize_workplace_type(part)
                if workplace_type:
                    return workplace_type
        except:
            pass

        try:
            main_content = self.page.locator('main').first
            if await main_content.count() > 0:
                text_elements = await main_content.locator('a, button, span, div, p').all()
                for elem in text_elements:
                    text = (await elem.inner_text()).strip()
                    if not text or len(text) > 80:
                        continue
                    workplace_type = self._normalize_workplace_type(text)
                    if workplace_type:
                        return workplace_type
        except:
            pass

        return None
    
    async def _get_posted_date(self) -> Optional[str]:
        """Extract posted date from job details."""
        try:
            parts = await self._get_metadata_parts()
            for part in parts:
                if POSTED_DATE_PATTERN.search(part):
                    return part.strip().split('\n')[0].strip()
        except:
            pass
            
        try:
            text_elements = await self.page.locator('span, div').all()
            for elem in text_elements:
                text = await elem.inner_text()
                if text and ('ago' in text.lower() or 'day' in text.lower() or 'week' in text.lower() or 'hour' in text.lower()):
                    text = text.strip()
                    if len(text) < 50:
                        return text
        except:
            pass
        return None
    
    async def _get_applicant_count(self) -> Optional[str]:
        """Extract applicant count from job details."""
        try:
            parts = await self._get_metadata_parts()
            for part in parts:
                if APPLICANT_PATTERN.search(part):
                    return part.strip().split('\n')[0].strip()
        except:
            pass
            
        try:
            main_content = self.page.locator('main').first
            if await main_content.count() > 0:
                text_elements = await main_content.locator('span, div').all()
                for elem in text_elements:
                    text = await elem.inner_text()
                    text = text.strip()
                    if text and len(text) < 50:
                        text_lower = text.lower()
                        if 'applicant' in text_lower or 'people clicked' in text_lower or 'applied' in text_lower:
                            return text
        except:
            pass
        return None
    
    async def _get_description(self) -> Optional[str]:
        """Extract job description from article or about section."""
        for selector in JOB_DESCRIPTION_SELECTORS:
            try:
                description_elem = self.page.locator(selector).first
                await description_elem.wait_for(state='attached', timeout=3000)
                if await description_elem.count() > 0:
                    description = await description_elem.inner_text()
                    if description and description.strip():
                        return self._clean_description(description)
            except:
                continue

        try:
            expandable = self.page.locator('[data-testid="expandable-text-box"]').first
            await expandable.wait_for(state='attached', timeout=3000)
            if await expandable.count() > 0:
                description = await expandable.inner_text()
                if description and description.strip():
                    return self._clean_description(description)
        except:
            pass

        try:
            about_heading = self.page.locator('h2:has-text("About the job")').first
            if await about_heading.count() > 0:
                article = about_heading.locator('xpath=ancestor::article[1]')
                if await article.count() > 0:
                    description = await article.inner_text()
                    return self._clean_description(description)

            article = self.page.locator('article').first
            if await article.count() > 0:
                description = await article.inner_text()
                return self._clean_description(description)
        except:
            pass
        return None

    def _clean_description(self, description: str) -> str:
        """Remove duplicated section labels such as 'Job Summary' from the beginning."""
        lines = [line.rstrip() for line in description.strip().splitlines()]

        while lines and not lines[0].strip():
            lines.pop(0)

        while lines and lines[0].strip().lower() in {"about the job", "job summary"}:
            lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)

        if lines:
            lines[0] = DESCRIPTION_PREFIX_PATTERN.sub("", lines[0]).strip()
            if not lines[0].strip():
                lines.pop(0)

        cleaned = "\n".join(lines).strip()
        return cleaned if cleaned else description.strip()
