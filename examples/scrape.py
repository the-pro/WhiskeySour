import requests
from whiskeysour import WhiskeySour


print("--- Hacker News Top 10 ---")
r = requests.get("https://news.ycombinator.com/")
soup = WhiskeySour(r.text)

rows = soup.find_all("tr", class_="athing")
for i, row in enumerate(rows[:10], 1):
    link = row.select_one(".titleline a")
    if link:
        print(f"{i}. {link.get_text()} ({link.get('href', '')})")


print("\n--- Quotes to Scrape ---")
r = requests.get("https://quotes.toscrape.com/")
soup = WhiskeySour(r.text)

for q in soup.find_all("div", class_="quote")[:5]:
    text = q.find("span", class_="text").string
    author = q.find("small", class_="author").string
    tags = [t.string for t in q.find_all("a", class_="tag")]
    print(f'"{text}" - {author} [{", ".join(tags)}]')


print("\n--- Wikipedia: Web scraping ---")
r = requests.get(
    "https://en.wikipedia.org/wiki/Web_scraping",
    headers={"User-Agent": "ws-test/1.0"}
)
soup = WhiskeySour(r.text)


for p in soup.select("#mw-content-text .mw-parser-output > p"):
    text = p.get_text(strip=True)
    if len(text) > 100:
        print(text[:300] + "...")
        break


toc = soup.find("div", id="toc")
if toc:
    items = toc.find_all("a")[:8]
    print("\nTable of contents:")
    for a in items:
        print(f"  {a.get_text(strip=True)}")


print("\n--- Books to Scrape ---")
r = requests.get("https://books.toscrape.com/")
soup = WhiskeySour(r.text)

for book in soup.select("article.product_pod")[:5]:
    title = book.select_one("h3 a")["title"]
    price = book.select_one(".price_color").string
    rating = book.select_one("p.star-rating")
    stars = rating["class"][1] if rating else "?"
    print(f"  {title[:50]:50s} {price:>8s}  ({stars} stars)")

print("\nDone - all pages scraped successfully.")
