<p align="center"><img src="https://gitlab.com/10ho/wastream/-/raw/main/wastream/public/wastream-logo.jpg" width="150"></p>

<p align="center">
  <a href="https://gitlab.com/10ho/wastream/-/releases">
    <img alt="GitLab release" src="https://img.shields.io/gitlab/v/release/10ho%2Fwastream?style=flat-square&logo=gitlab&logoColor=white&labelColor=1C1E26&color=4A5568">
  </a>
  <a href="https://www.python.org/">
    <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11+-blue?style=flat-square&logo=python&logoColor=white&labelColor=1C1E26&color=4A5568">
  </a>
  <a href="https://gitlab.com/10ho/wastream/-/blob/main/LICENSE">
    <img alt="License" src="https://img.shields.io/gitlab/license/10ho%2Fwastream?style=flat-square&labelColor=1C1E26&color=4A5568">
  </a>
</p>

<p align="center">
  <strong>Unofficial Stremio addon that resolves direct download links (DDL) into streamable URLs through debrid services</strong>
</p>

---

## ⚠️ Important Notice

**WAStream is a generic link-resolution utility.** It is a technical tool that takes a direct download link as input and returns a streamable URL as output, using debrid services as an intermediary.

**This tool does not host, distribute, provide, or promote any content.** It is content-agnostic by design: it simply processes whatever link the user configures it to process.

**Legitimate use cases include:**
- Streaming your own personal backups stored on file-hosting services
- Accessing public domain or Creative Commons content
- Self-hosted storage and personal file management
- Open source software distributed via DDL
- Any content the user legally has the right to access

**By using this tool, you agree that:**
- You are solely responsible for ensuring you have the legal right to access any content you process
- You must comply with all applicable copyright laws in your jurisdiction
- You must respect the terms of service of any sources and services you configure
- The developer disclaims any responsibility for misuse of this tool

---

## About

WAStream is a Stremio addon that converts direct download links into instant streamable content through debrid services. It provides a generic, configurable scraping and resolution pipeline that can be adapted to any DDL source via environment variables.

### How It Works

1. You configure one or more sources via environment variables
2. The addon queries these sources when Stremio requests streams for a given title
3. The addon converts the resulting direct download links into streamable URLs via the debrid service of your choice
4. Stremio plays the resulting stream

### What WAStream Does

- **Generic source scraping**: Configurable scraper interface that works with any compatible source
- **Debrid integration**: Resolves DDL through debrid services into streamable URLs
- **Smart caching**: Intelligent cache system with distributed locking for optimal performance
- **Instant availability check**: Verifies link availability before streaming
- **Advanced filtering**: Filter by quality, language, size, and more
- **Multi-language support**: Handles multiple audio tracks and subtitle languages
- **Metadata enrichment**: Integrates TMDB and Kitsu for comprehensive metadata

---

## Features

### Supported Content Types

- **Movies** / **Series** / **Anime**

### Supported Debrid Services

- **AllDebrid** - Multi-host support
- **TorBox** - Multi-host support
- **Premiumize** - Multi-host support
- **1Fichier** - Direct 1Fichier premium
- **NZBDav** - Usenet via SABnzbd + WebDAV

### Smart Features

- **Instant cache check** - Verifies availability before conversion
- **Round-robin processing** - Optimized link checking algorithm
- **Quality filtering** - Filter by resolution (4K, 1080p, 720p, etc.)
- **Language filtering** - Multi-language and subtitle support
- **Size filtering** - Filter by file size
- **Deduplication** - Hide identical results from different hosters
- **Password protection** - Optional addon password protection
- **Database support** - SQLite or PostgreSQL
- **Pastebin scraper** - Auto-import links from external paste URLs on a schedule
- **HTTP response caching** - Optional ETag-based caching for stream responses
- **Kitsu integration** - Anime metadata via Kitsu API with season chain mapping

### Admin Dashboard

- **Statistics** - Users, searches, streams, cache stats
- **Live logs** - Real-time log viewer with filtering
- **Source status** - Monitor source and proxy health
- **System info** - RAM/CPU usage, database status
- **Dead links management** - Add/remove dead links
- **WASource** - Add custom links compatible with all debrid services
- **Remote system** - Share dead links, cache and custom links between instances
- **Pastebin scraper** - Status and stats of auto-import jobs

---

## Installation

> **For environment configuration, see [`.env.example`](.env.example)**

### Docker Compose (Recommended)

1. **Create a `docker-compose.yml` file**:

```yaml
services:
  wastream:
    image: registry.gitlab.com/10ho/wastream:latest
    container_name: wastream
    ports:
      - "7000:7000"
    volumes:
      - ./data:/app/data
    environment:
      - SECRET_KEY=your-secret-key-min-32-chars # Required - Generate with: openssl rand -hex 32
      - ADMIN_PASSWORD=your-admin-password-here # (Optional) Password for admin dashboard - Leave empty to disable admin
      - DATABASE_TYPE=sqlite
      - DATABASE_PATH=/app/data/wastream.db
      # Configure your sources via environment variables (see .env.example)
    restart: unless-stopped
```

2. **Start the container**:
```bash
docker-compose up -d
```

3. **Check logs**:
```bash
docker-compose logs -f wastream
```

### Manual Installation

#### Prerequisites
- Python 3.11 or higher
- Git

#### Steps

1. **Clone the repository**:
```bash
git clone https://gitlab.com/10ho/wastream.git
cd wastream
```

2. **Install dependencies**:
```bash
pip install .
```

3. **Configure environment**:
```bash
cp .env.example .env
# Edit .env according to your needs
```

4. **Start the application**:
```bash
python -m wastream.main
```

---

## Configuration

### Add to Stremio

1. **Access** `http://localhost:7000` in your browser
2. **Configure** your debrid API keys and TMDB token
3. **Click** "Generate link"

The addon will appear in your Stremio addon list.

### Admin Dashboard

1. **Set** `ADMIN_PASSWORD` in your `.env` file
2. **Access** `http://localhost:7000/admin` in your browser
3. **Login** with your admin password

### Environment Variables

See [`.env.example`](.env.example) for all available configuration options.

**Required:**
- `SECRET_KEY` - Encryption key for user configs (min 32 chars) - Generate with: `openssl rand -hex 32`
- At least one source URL must be configured (see `.env.example`)
- Debrid API key - Configured via web interface
- TMDB API token - Configured via web interface

**Optional:**
- `ADMIN_PASSWORD` - Password for admin dashboard
- `ADDON_PASSWORD` - Password protect your addon
- `LOG_LEVEL` - Set log level (DEBUG, INFO, ERROR)
- `PROXY_URL` - HTTP proxy for source access
- `PASTEBIN_SCRAPER_URLS` - JSON list of paste URLs for auto-import
- And many more... (see `.env.example`)

---

## Troubleshooting

### Debug

```bash
# Health check
curl http://localhost:7000/health

# Docker logs
docker-compose logs -f wastream

# Enable debug mode
LOG_LEVEL=DEBUG python -m wastream.main
```

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---

## Disclaimer

**WAStream is an unofficial, independently developed project.**

- **Not affiliated with Stremio or any source/service provider**
- **Provided "as is" without any warranty of any kind**
- **No content is hosted, distributed, or promoted by this project**
- **The developer takes no responsibility for how users choose to use this tool**

This is a technical tool with legitimate uses. It is the sole responsibility of the user to ensure that their usage complies with applicable copyright laws, terms of service, and regulations in their jurisdiction. The developer does not endorse, support, or condone any unlawful use of this software.

If you are a rights holder and believe this tool is being misused, please note that this repository only contains a generic link-resolution utility and does not host, link to, or reference any specific copyrighted content. Any takedown requests should be directed at the actual sources of infringing content, not at this technical tool.

---
