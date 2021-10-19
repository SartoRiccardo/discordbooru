import json
import os
import logging
import requests
import sys, traceback
import time
from mimetypes import guess_type
from itertools import chain
from collections import defaultdict
from datetime import datetime
from dateutil.parser import isoparse
from dateutil.tz import UTC
from retry import retry
from pybooru import Danbooru
from config import USERNAME, API_KEY, DANBOORU_URL_BASE, PIXIV_URL_BASE, TAG_BLACKLIST, \
        SOURCE_BLACKLIST, IMAGE_TYPES


class DanbooruRating:
    SFW = 's'
    LEWD = 'q'
    NSFW = 'e'


SLEEP_TIME = 60
logger = None
client = Danbooru('danbooru', username=USERNAME, api_key=API_KEY)

# These are for ratelimits while posting.
ratelimit_left = 0
ratelimit_wait = 0


def setup_logger():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(levelname)s - %(message)s')
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    handler = logging.FileHandler('output.log', 'w', )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def convert_to_utc(date_string):
    d = isoparse(date_string)
    return d.astimezone(UTC).isoformat()[:-6] + 'Z'


def check_blacklist(post, feed):
    # Check for tags individually in both global and feed-specific blacklists.
    post_tags = post['tag_string'].split()
    for tag in chain(TAG_BLACKLIST, feed['blacklist']):
        if tag in post_tags:
            return f"Rejected post {post['id']} in {feed['name']}, contains blacklisted tag {tag}"

    # Check if the post source matches any of the listed blacklisted sources.
    for source in SOURCE_BLACKLIST:
        if source in post['source']:
            return f"Rejected post {post['id']} in {feed['name']}, contains blacklisted source {source}"

    # Check if the post is NSFW in a SFW feed.
    if post["rating"] != "s" and not feed["is_nsfw"]:
        return f"Rejected post {post['id']} in {feed['name']}, is NSFW in a SFW feed"

    # Check if the post is SFW in a NSFW-only feed.
    if post["rating"] == "s" and feed["is_nsfw"] and feed["only_nsfw"]:
        return f"Rejected post {post['id']} in {feed['name']}, is SFW in a NSFW-only feed"

    return None


# Pixiv source links work weirdly in Danbooru's API.
# Unlike most other source links, they show up as hotlinks directly to the
# image instead of as links to the page for the artwork. However, Danbooru
# does have a field for the artwork's Pixiv ID where applicable, so if the
# post has a Pixiv ID, just create the Pixiv URL ourselves using the usual
# base artwork URL for Pixiv posts and the ID Danbooru supplies us with.
def source_link(post):
    if post['pixiv_id'] is not None:
        return PIXIV_URL_BASE + str(post['pixiv_id'])
    return post['source']


def generate_embed(post, feed):
    if guess_type(post['file_url'])[0].startswith('image'):
        post_url = post['file_url']
    else:
        post_url = post['preview_file_url']

    return {'embeds': [{
        'title': f"New post in {feed['name']}",
        'url': DANBOORU_URL_BASE + str(post['id']),
        'color': int(feed['color'], 16),
        'timestamp': convert_to_utc(post['created_at']),
        'image': {'url': post_url},
        'footer': {'text': f"ID: {post['id']}"},
        'fields': [{
           'name': 'Source',
           'value': source_link(post)
        }]
    }]}


def queue_posts(results, feed, recent: int):
    """
    Gather new posts
    :param results: The results from the previous pages.
    :param feed: Data about the feed we want to retrieve.
    :param recent: The ID of the most recent post we have.
    :return: List: A list of new posts.
    """
    # Check if there's even any results.
    if not results:
        logger.info(f"Current results page for {feed['name']} empty")
        return []

    # Any posts that we find are new will be put in here and returned.
    new_posts = []
    for post in results:
        # Check post against tag/source blacklist, if true there's a match.
        blacklist_reason = check_blacklist(post, feed)
        if blacklist_reason:
            logger.info(blacklist_reason)
            continue

        # Danbooru post IDs are incremental and sorted newest to oldest.
        if post['id'] <= recent:
            logger.info(f"{len(new_posts)} posts in {feed['name']} queue")
            return new_posts
        # If both above are false, add the post to the queue.
        logger.info(f"Adding post id {post['id']} to {feed['name']} queue")
        new_posts.append(generate_embed(post, feed))

    # Out of options and desperate for answers, I booked a flight to the next page
    logger.info(f"Reached end of page for {feed['name']}, going to next")
    next_page_url = feed['booru'] + '&page=b' + str(results[len(new_posts)-1]['id'])
    next_page_results = client.post_list(page=next_page_url, tags=feed["tags"])
    next_page_queue = queue_posts(next_page_results, feed, recent)
    new_posts.extend(next_page_queue)
    logger.info(f"{len(new_posts)} posts in {feed['name']} queue")
    return new_posts


@retry(requests.exceptions.HTTPError, tries=5, delay=1, backoff=2, jitter=(0, 2), logger=logger)
def make_post(feed, post):
    global ratelimit_left, ratelimit_wait
    try:
        webhook_post = requests.post(feed['webhook'], json=post)
        logger.info(f"Response code {webhook_post.status_code} for post in {feed['name']}")
        webhook_post.raise_for_status()

        # Get ratelimit info: number of posts remaining before
        # we hit the limit, and time left before remaining posts
        # count resets.
        ratelimit_left = int(webhook_post.headers['X-RateLimit-Remaining'])
        ratelimit_wait = int(webhook_post.headers['X-RateLimit-Reset-After'])

        # If there's no posts remaining, we'll wait for the reset.
        if ratelimit_left <= 0:
            logger.info(f'Waiting {ratelimit_wait} seconds to next post...')
            time.sleep(ratelimit_wait)
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            global_wait_time = float(e.response.headers['retry-after'])/1000
            logger.warning(f'Global rate limit reached, waiting {global_wait_time} seconds to next post...')
            time.sleep(global_wait_time)
        raise


def check_feed(feed, recents):
    logger.info(f"Checking new posts for {feed['name']}")
    # Get the most recent search results for this feed.
    booru_results = client.post_list(tags=feed["tags"])

    # Check if the feed even has a most recent post ID.
    if feed['name'] in recents.keys():
        posts = queue_posts(booru_results, feed, recents[feed['name']])
        recents[feed['name']] = booru_results[0]['id']
        for post in reversed(posts):
            make_post(feed, post)
    else:
        # If the feed doesn't have a most recent post ID, that probably
        # means it's newly added so let's say the most recent ID is
        # whatever the newest post is and not post anything.
        logger.info(f"Adding new feed {feed['name']}")
        recents[feed['name']] = booru_results[0]['id']
        # This should only happen once, after the feed is added.
        # From now on, the if statement should be True.


def is_running():
    has_to_stop = os.path.exists("STOP-DISCORDBOORU")
    if has_to_stop:
        os.remove("STOP-DISCORDBOORU")
    return not has_to_stop



def main():
    global logger
    logger = setup_logger()

    while is_running():
        logger.info('Beginning new cycle')

        # Open list of feeds and associated parameters
        with open('feeds.json', 'r') as feeds_file:
            feeds = json.load(feeds_file)

        # Now open a file of the most recent post IDs for each feed
        # The file will remain open to update the recent IDs when we're done
        with open('recents.json', 'r+') as recents_file:
            recents = defaultdict(int, json.load(recents_file))
            # Check and make new posts for each feed
            for feed in feeds:
                try:
                    check_feed(feed, recents)
                except Exception as e:
                    logger.error(e)

            # Finally, update the file with the new IDs
            recents_file.seek(0)
            recents_file.truncate()
            json.dump(recents, recents_file)
            logger.info('Cycle complete, recents file updated')

        logger.info(f'Waiting {SLEEP_TIME} seconds to next cycle...')
        time.sleep(SLEEP_TIME)


if __name__ == '__main__':
    main()
