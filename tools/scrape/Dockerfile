# Manual web scraper for the knowledge base (see README "Adding web pages to
# the knowledge base"). Kept out of the chat-ui image: trafilatura is only
# needed when someone captures new pages.
FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt pytest==9.2.0

COPY . .

ENTRYPOINT ["python", "scrape.py"]
