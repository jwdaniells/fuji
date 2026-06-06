import json, base64, re, time, os, subprocess
import urllib.request

tok  = os.environ["GH_TOKEN"]
repo = "jwdaniells/fuji"
api  = f"https://api.github.com/repos/{repo}/contents"
hdrs = {"Authorization": f"token {tok}", "Content-Type": "application/json"}

def api_get(path):
    req = urllib.request.Request(api + path, headers=hdrs)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def api_put(path, msg, b64, sha=None):
    body = {"message": msg, "content": b64}
    if sha: body["sha"] = sha
    data = json.dumps(body).encode()
    req = urllib.request.Request(api + path, data=data, method="PUT", headers=hdrs)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def fetch_page_curl(url):
    result = subprocess.run([
        "curl", "-sL", "--max-time", "20",
        "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "-H", "Accept-Language: en-US,en;q=0.9",
        url
    ], capture_output=True, text=True, timeout=25)
    return result.stdout if result.returncode == 0 else None

def get_og_image(source_url):
    html = fetch_page_curl(source_url)
    if not html:
        return None

    # og:image (most reliable)
    m = re.search(r'property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html)
    if not m:
        m = re.search(r'content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html)
    if m:
        url = m.group(1)
        if "270%2C270" not in url and "cropped" not in url and "app-store" not in url:
            return url

    # First image in body
    skip = ["cropped", "logo", "icon", "270x270", "app-store", "img_5106", "android", "apple-app"]
    for img in re.findall(r'https://[^\s"]+/wp-content/uploads/\d{4}/\d{2}/[^\s"]+\.jpg', html):
        if not any(s in img for s in skip):
            return img
    for img in re.findall(r'https://film\.recipes/wp-content/uploads/[^\s"]+\.jpg', html):
        if not any(s in img for s in skip):
            return img
    for img in re.findall(r'https://static\.wixstatic\.com/media/[^\s"]+\.jpg', html):
        return img
    return None

def download_image_curl(img_url, referer):
    result = subprocess.run([
        "curl", "-sL", "--max-time", "30",
        "-A", "Mozilla/5.0",
        "-H", f"Referer: {referer}",
        "-o", "/tmp/recipe_img.jpg",
        img_url
    ], capture_output=True, timeout=35)
    if result.returncode == 0:
        with open("/tmp/recipe_img.jpg", "rb") as f:
            data = f.read()
        if len(data) > 5000:
            return data
    return None

# Load data.json
print("Loading data.json...")
d = api_get("/recipes/data.json")
data = json.loads(base64.b64decode(d["content"].replace("\n", "")))

# Process recipes with no image OR an external URL image (starts with http)
to_process = []
for r in data["recipes"]:
    img = r.get("image", "")
    if not img and r.get("source_url"):
        to_process.append((r["id"], r["name"], r.get("source_url", ""), None))
    elif img and img.startswith("http") and r.get("source_url"):
        # Has external URL — download it directly rather than scraping source page
        to_process.append((r["id"], r["name"], r.get("source_url", ""), img))

print(f"Processing {len(to_process)} recipes (missing or external-URL images)...")
success = []
no_img = []

for i, (rid, name, src_url, direct_url) in enumerate(to_process):
    print(f"[{i+1}/{len(to_process)}] {name}")

    if direct_url:
        # Use the stored external URL directly
        img_url = direct_url
        referer = src_url
        print(f"  Direct URL: {img_url[:80]}")
    else:
        # Scrape source page for og:image
        img_url = get_og_image(src_url)
        if not img_url:
            print(f"  No image URL found")
            no_img.append(name)
            time.sleep(0.5)
            continue
        referer = "https://fujixweekly.com/" if "fujixweekly" in src_url else src_url
        print(f"  Scraped: {img_url[:80]}")

    img_data = download_image_curl(img_url, referer)
    if not img_data:
        print(f"  Download failed")
        no_img.append(name)
        time.sleep(0.5)
        continue

    img_path = f"/images/{rid}.jpg"
    img_b64 = base64.b64encode(img_data).decode()
    try:
        try:
            ex = api_get(img_path)
            api_put(img_path, f"Add image: {name}", img_b64, ex["sha"])
        except Exception:
            api_put(img_path, f"Add image: {name}", img_b64)
        print(f"  Committed ({len(img_data)//1024}KB)")
    except Exception as e:
        print(f"  Commit failed: {e}")
        no_img.append(name)
        time.sleep(1)
        continue

    for recipe in data["recipes"]:
        if recipe["id"] == rid:
            recipe["image"] = f"{rid}.jpg"
            break

    success.append(name)
    time.sleep(1.0)

print(f"\nCommitting data.json ({len(success)} images added)...")
data["last_updated"] = "2026-06-06"
new_b64 = base64.b64encode(json.dumps(data, indent=2).encode()).decode()
d2 = api_get("/recipes/data.json")
api_put("/recipes/data.json", f"Add images for {len(success)} recipes", new_b64, d2["sha"])

print(f"\nDone: {len(success)} succeeded, {len(no_img)} failed")
if no_img:
    print("No image found:")
    for n in no_img:
        print(f"  - {n}")
