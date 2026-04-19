"""GitHub API utilities."""

import logging
from urllib.request import Request, urlopen
from urllib.error import URLError
import json

import yaml

logger = logging.getLogger(__name__)


def load_config():
    """Load configuration from config.yaml."""
    config_path = "config.yaml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.warning("config.yaml not found, using empty config")
        return {}


def get_repo_info(owner: str, repo: str) -> dict | None:
    """Fetch basic information for a GitHub repository.

    Args:
        owner: Repository owner username or organization.
        repo: Repository name.

    Returns:
        Dictionary containing stargazers_count, forks_count, and description,
        or None if the request fails.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {"Accept": "application/vnd.github.v3+json"}

    config = load_config()
    if token := config.get("github", {}).get("token"):
        headers["Authorization"] = f"token {token}"

    request = Request(url, headers=headers)
    try:
        with urlopen(request) as response:
            data = json.loads(response.read().decode("utf-8"))
            return {
                "stargazers_count": data.get("stargazers_count", 0),
                "forks_count": data.get("forks_count", 0),
                "description": data.get("description", ""),
            }
    except URLError as e:
        logger.error(f"Failed to fetch repo info for {owner}/{repo}: {e}")
        return None