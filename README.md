# Discordbooru
Danbooru posts to Discord with multiple webhooks.

## Installation
To install and run this bot, first clone this repository.
```bash
git clone https://github.com/SartoRiccardo/discordbooru.git
cd discordbooru
```

Then, install all dependencies needed for the project, listed in `requirements.txt`.
```bash
pip install -r requirements.txt
```

## Configuration
First, rename the `config.example.py` and the `feeds.example.json` to `config.py` and `feeds.json`, respectively
```bash
mv config.example.py config.py
mv feeds.example.json feeds.json
```

### `config.py`
To start this bot, you first need a Danbooru account along with its API key, which you can easily get by signing up to [their website](https://danbooru.donmai.us/).
Once you have created your account and API key, replace the `USERNAME` and `API_KEY` constants with your information.
```python
USERNAME = "BotAccount529"
API_KEY = "pa3BE91...FaGR"
...
```

### `feeds.json`
Follow the examples in `feeds.example.json` to create your own feeds. Here is the list of fields a feed object can have:
| Field name | Description |
| - | - |
| `name` | The name of the feed, shown as the title of the embed. |
| `tags` | The tags that Danbooru will look at for the feed. Note that for a post to be elegible, it has to fit **all tags** in this array. |
| `webhook` | The URL of the Discord webhook to post danbooru art to. |
| `blacklist` | feed-specific blacklisted tags. |
| `color` | The color of the Discord embed. |
| `is_nsfw` | If set to `true`, the bot will also post images marked as NSFW. |
| `only_nsfw` | If set to `true`, the bot will *only* post images marked as NSFW, ignoring SFW ones. This field is ignored if `is_nsfw` is set to `false`. |
