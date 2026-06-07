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

def fetch_curl(url, extra_headers=None):
    cmd = [
        "curl", "-sL", "--max-time", "20",
        "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "-H", "Accept-Language: en-US,en;q=0.9",
    ]
    if extra_headers:
        for h in extra_headers:
            cmd += ["-H", h]
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    return result.stdout if result.returncode == 0 else None

def fetch_json(url):
    """Fetch JSON from WP REST API"""
    cmd = [
        "curl", "-sL", "--max-time", "20",
        "-H", "Accept: application/json",
        "-A", "Mozilla/5.0",
        url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    if result.returncode == 0 and result.stdout.strip():
        try:
            return json.loads(result.stdout)
        except:
            return None
    return None

def get_wp_featured_image(source_url):
    """Use WordPress REST API to get featured image URL"""
    # Extract slug from URL
    slug = source_url.rstrip('/').split('/')[-1]
    
    # Try WP REST API
    wp_api = f"https://fujixweekly.com/wp-json/wp/v2/posts?slug={slug}&_fields=id,featured_media"
    data = fetch_json(wp_api)
    if not data or not isinstance(data, list) or not data:
        return None
    
    post = data[0]
    media_id = post.get('featured_media')
    if not media_id:
        return None
    
    # Get media URL
    media_api = f"https://fujixweekly.com/wp-json/wp/v2/media/{media_id}?_fields=source_url,media_details"
    media_data = fetch_json(media_api)
    if not media_data:
        return None
    
    # Try to get a large size
    sizes = media_data.get('media_details', {}).get('sizes', {})
    for size in ['large', 'medium_large', 'medium', 'full']:
        if size in sizes:
            url = sizes[size].get('source_url')
            if url:
                return url
    
    # Fall back to source_url
    return media_data.get('source_url')

def get_og_image(source_url):
    """Get og:image from page HTML"""
    html = fetch_curl(source_url)
    if not html or len(html) < 100:
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
    for img in re.findall(r'https://images\.squarespace-cdn\.com/content/[^\s"]+\.jpg', html):
        if not any(s in img for s in skip):
            return img
    return None

def get_image_url(rid, source_url):
    """Try multiple strategies to get image URL"""
    
    # Strategy 1: WordPress REST API (for fujixweekly)
    if 'fujixweekly.com' in source_url:
        img_url = get_wp_featured_image(source_url)
        if img_url:
            print(f"  [WP API] {img_url[:80]}")
            return img_url, source_url
    
    # Strategy 2: Scrape og:image from page
    img_url = get_og_image(source_url)
    if img_url:
        referer = "https://fujixweekly.com/" if "fujixweekly" in source_url else source_url
        print(f"  [og:image] {img_url[:80]}")
        return img_url, referer
    
    return None, None

def download_image_curl(img_url, referer):
    result = subprocess.run([
        "curl", "-sL", "--max-time", "30",
        "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "-H", f"Referer: {referer}",
        "-H", "Accept: image/webp,image/apng,image/*,*/*;q=0.8",
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

# Process recipes with no image OR an external URL image
to_process = []
for r in data["recipes"]:
    img = r.get("image", "")
    if not img and r.get("source_url"):
        to_process.append((r["id"], r["name"], r.get("source_url", ""), None))
    elif img and img.startswith("http") and r.get("source_url"):
        to_process.append((r["id"], r["name"], r.get("source_url", ""), img))

print(f"Processing {len(to_process)} recipes...")
success = []
no_img = []

for i, (rid, name, src_url, direct_url) in enumerate(to_process):
    print(f"[{i+1}/{len(to_process)}] {name}")

    if direct_url:
        img_url = direct_url
        referer = src_url
        print(f"  Direct URL: {img_url[:80]}")
    else:
        img_url, referer = get_image_url(rid, src_url)
        if not img_url:
            print(f"  No image URL found")
            no_img.append(name)
            time.sleep(0.5)
            continue

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
import datetime
data["last_updated"] = datetime.date.today().isoformat()
new_b64 = base64.b64encode(json.dumps(data, indent=2).encode()).decode()
d2 = api_get("/recipes/data.json")
api_put("/recipes/data.json", f"Add images for {len(success)} recipes", new_b64, d2["sha"])

print(f"\nDone: {len(success)} succeeded, {len(no_img)} failed")
if no_img:
    print("No image found:")
    for n in no_img:
        print(f"  - {n}")
