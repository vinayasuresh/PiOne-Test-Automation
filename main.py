import getpass
from datetime import datetime
from urllib.parse import urlparse

from automation_framework.config.settings import STORAGE_STATE_PATH
from automation_framework.crawler.crawl_exporter import export_crawl_results
from automation_framework.crawler.crawler_engine import CrawlerEngine
from automation_framework.crawler.interaction_recorder import record_manual_interactions
from automation_framework.crawler.login_handler import login
from automation_framework.crawler.navigation_expander import ensure_navigation_expanded
from automation_framework.utils.browser_manager import start_persistent_browser
from automation_framework.utils.logger import configure_run_logger, logger
from automation_framework.utils.metadata_collector import (
    collect_metadata,
    generate_run_id,
    save_metadata,
)


def _prompt_base_url() -> str | None:
    """Prompt the user for the application URL and validate it.

    Returns the validated URL, or None if the input is invalid.
    """
    base_url = input("Enter application URL: ").strip()
    if not base_url:
        print("ERROR: Application URL cannot be empty.")
        return None

    parsed = urlparse(base_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        print("ERROR: Application URL must start with http:// or https:// and include a domain.")
        return None

    return base_url


def main() -> None:
    run_id = generate_run_id()
    log_file = configure_run_logger(run_id)
    started_at = datetime.now()
    crawl_results: dict = {}
    base_url = ""

    playwright = None
    browser = None
    context = None

    try:
        base_url = _prompt_base_url()
        if not base_url:
            return

        username = input("Enter username: ").strip()
        password = getpass.getpass("Enter password: ")

        logger.info(
            f"Framework startup | run_id={run_id} | log_file={log_file} | base_url={base_url}"
        )
        playwright, browser, context, page = start_persistent_browser(STORAGE_STATE_PATH)

        logger.info("Login started")
        login(page, username, password, base_url)
        context.storage_state(path=str(STORAGE_STATE_PATH))
        logger.info("Login successful")

        logger.info(f"Current URL after login: {page.url}")
        print(f"Current URL: {page.url}")

        try:
            logger.info("Expanding navigation if needed")
            ensure_navigation_expanded(page)

            logger.info("Crawler execution started")
            crawler = CrawlerEngine(
                page,
                base_url=base_url,
                credentials=(username, password),
            )
            crawl_results = crawler.crawl(start_url=page.url)

            manual_choice = input("Start assisted manual exploration? [y/N]: ").strip().lower()
            if manual_choice == "y":
                crawl_results["manual_interactions"] = record_manual_interactions(page)

            export_paths = export_crawl_results(crawl_results, run_id=run_id)

            route_count = len(crawl_results.get("routes", []))
            feature_count = len(crawl_results.get("feature_routes", {}))
            component_count = sum(
                len(route_doc.get("automation_targets", []))
                for route_doc in crawl_results.get("page_intelligence", {}).values()
            )
            manual_interaction_count = len(crawl_results.get("manual_interactions", []))

            logger.info("Crawler execution completed")
            print("Crawl Summary:")
            print(f"Routes crawled: {route_count}")
            print(f"Feature routes explored: {feature_count}")
            print(f"Automation targets detected: {component_count}")
            print(f"Manual interactions captured: {manual_interaction_count}")
            print(f"JSON report: {export_paths['json']}")
            print(f"YAML report: {export_paths['yaml']}")
        except Exception:
            logger.exception("Crawler execution failed")
            print("ERROR: Crawler execution failed. Check logs for details.")
            return
    except Exception:
        logger.exception("Framework execution failed")
        raise
    finally:
        if context:
            context.storage_state(path=str(STORAGE_STATE_PATH))

        if browser:
            logger.info("Browser shutdown")
            browser.close()

        if playwright:
            playwright.stop()

        finished_at = datetime.now()
        try:
            metadata = collect_metadata(
                run_id, started_at, finished_at, crawl_results, base_url=base_url
            )
            metadata_path = save_metadata(run_id, metadata)
            print(f"Run metadata: {metadata_path}")
            print(f"Run log:      {log_file}")
        except Exception:
            logger.exception("Failed to save run metadata")


if __name__ == "__main__":
    main()
