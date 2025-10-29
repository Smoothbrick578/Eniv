from flask import Flask, render_template, request, redirect, session, url_for, jsonify, abort
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timezone
from moviepy.editor import VideoFileClip
import os, json
import ffmpeg
import uuid
import random
import string

app = Flask(__name__)
app.secret_key = "supersecretkey"  # change this

VIDEO_FOLDER = "static/videos"
THUMB_FOLDER = "static/thumbnails"
USER_FILE = "users.json"
ADMIN_FILE = "admins.json"

os.makedirs(VIDEO_FOLDER, exist_ok=True)
os.makedirs(THUMB_FOLDER, exist_ok=True)

# ------------------------------
# Helper functions
# ------------------------------
def load_users():
    if not os.path.exists(USER_FILE):
        return {}
    with open(USER_FILE, "r") as f:
        return json.load(f)

def save_users(users):
    with open(USER_FILE, "w") as f:
        json.dump(users, f, indent=2)

def time_since(uploaded):
    from datetime import datetime, timezone

    # Handle both str and datetime inputs
    if isinstance(uploaded, str):
        try:
            uploaded = datetime.fromisoformat(uploaded)
        except Exception:
            return "unknown time"
    elif not isinstance(uploaded, datetime):
        return "unknown time"

    now = datetime.now(timezone.utc)
    if uploaded.tzinfo is None:
        uploaded = uploaded.replace(tzinfo=timezone.utc)
    delta = now - uploaded

    seconds = delta.total_seconds()
    if seconds < 60:
        return f"{int(seconds)} seconds ago"
    elif seconds < 3600:
        return f"{int(seconds // 60)} minutes ago"
    elif seconds < 86400:
        return f"{int(seconds // 3600)} hours ago"
    elif seconds < 604800:
        return f"{int(seconds // 86400)} days ago"
    elif seconds < 2419200:
        return f"{int(seconds // 604800)} weeks ago"
    else:
        return uploaded.strftime("%b %d, %Y")

def ensure_user_fields(users):
    changed = False
    for u, data in list(users.items()):
        if isinstance(data, str):
            users[u] = {
                "password": data,
                "bio": "",
                "profile_pic": "",
                "followers": [],
                "following": [],
                "notifications": [],
                "shadowbanned": False
            }
            changed = True
        else:
            if "followers" not in data:
                data["followers"] = []
                changed = True
            if "following" not in data:
                data["following"] = []
                changed = True
            if "notifications" not in data:
                data["notifications"] = []
                changed = True
            if "shadowbanned" not in data:
                data["shadowbanned"] = False
                changed = True
    if changed:
        save_users(users)

def load_admins():
    if not os.path.exists(ADMIN_FILE):
        # create a default file with an empty lists
        default = {"admins": [], "moderators": []}
        with open(ADMIN_FILE, "w") as f:
            json.dump(default, f, indent=2)
        return default
    with open(ADMIN_FILE, "r") as f:
        return json.load(f)

def save_admins(admins_obj):
    with open(ADMIN_FILE, "w") as f:
        json.dump(admins_obj, f, indent=2)

def is_admin(username):
    if not username:
        return False
    admins_obj = load_admins()
    return username in admins_obj.get("admins", [])

def is_moderator(username):
    if not username:
        return False
    admins_obj = load_admins()
    return username in admins_obj.get("moderators", [])

# ------------------------------
# In-memory video storage
# ------------------------------
videos = []

# ------------------------------
# Routes
# ------------------------------
VIDEO_FILE = "videos.json"

def load_videos():
    if not os.path.exists(VIDEO_FILE):
        return []
    with open(VIDEO_FILE, "r") as f: 
        return json.load(f)

def save_videos(videos):
    with open(VIDEO_FILE, "w") as f:
        json.dump(videos, f, indent=2, ensure_ascii=False)

@app.route("/videos")
def get_videos():
    videos = load_videos()
    return jsonify({"videos": videos})

@app.route("/video/<video_id>")
def video_page(video_id):
    videos = load_videos()
    video = next((v for v in videos if v["id"] == video_id), None)
    if not video:
        return "Video not found", 404

    # Increment view count
    video["views"] = video.get("views", 0) + 1
    save_videos(videos)

    description = video.get("description")
    
    username = session.get("username")
    logged_in = "username" in session
    user_liked = username in video.get("liked_by", []) if username else False
    user_disliked = username in video.get("disliked_by", []) if username else False

    uploaded_ago = time_since(video["uploaded_at"])

    def mark_comment_votes(comments):
        for c in comments:
            c["current_user_liked"] = username in c.get("liked_by", []) if username else False
            c["current_user_disliked"] = username in c.get("disliked_by", []) if username else False
            if c.get("replies"):
                mark_comment_votes(c["replies"])

    mark_comment_votes(video.get("comments", []))

    return render_template(
        "video.html",
        video=video,
        description=video.get("description"),
        logged_in=logged_in,
        username=username,
        user_liked=user_liked,
        user_disliked=user_disliked,
        uploaded_ago=uploaded_ago
    )

@app.route("/")
def index():
    sort_by = request.args.get("sort", "newest")
    search_query = request.args.get("q", "").lower().strip()
    username = session.get("username")
    logged_in = "username" in session
    videos = load_videos()
    users = load_users()
    ensure_user_fields(users)

    def visible_in_search(video, viewer):
        uploader = video.get("uploader")
        if not uploader:
            return False
        
        uploader_data = users.get(uploader, {})
        if uploader_data.get("shadowbanned", False):
            return False
        
        # Admin bypass (optional — remove if you want admins to filter too)
        if is_admin(viewer):
            pass  # allow search normally
        
        # Check title, description, and uploader for the query
        query = search_query.lower().strip()
        title = video.get("title", "").lower()
        description = video.get("description", "").lower()
        uploader_lower = uploader.lower()
        
        return query in title or query in description or query in uploader_lower

    # Filter videos by search query if present
    if search_query:
        videos = [v for v in videos if visible_in_search(v, session.get("username"))]

    # Convert uploaded_at to datetime for proper sorting
    for v in videos:
        try:
            v["_uploaded_dt"] = datetime.fromisoformat(v["uploaded_at"])
        except Exception:
            v["_uploaded_dt"] = datetime.min  # fallback if invalid

        v["uploaded_ago"] = time_since(v["uploaded_at"])

    # Sort videos
    if sort_by == "views":
        videos = sorted(videos, key=lambda v: v.get("views", 0), reverse=True)
    elif sort_by == "likes":
        videos = sorted(videos, key=lambda v: v.get("likes", 0), reverse=True)
    else:  # newest
        videos = sorted(videos, key=lambda v: v["_uploaded_dt"], reverse=True)

    return render_template(
        "index.html",
        logged_in=logged_in,
        username=username,
        videos=videos,
        time_since=time_since,
        current_sort=sort_by,
        search_query=search_query
    )

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        recovery_code = request.form.get("recovery_code")

        users = load_users()
        if username in users:
            return "That username already exists."

        hashed = generate_password_hash(password)
        users[username] = {
            "password": hashed,
            "bio": "",
            "profile_pic": None,
            "hint": request.form.get("hint", "")
        }
        save_users(users)

        session["username"] = username
        return redirect("/")

    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()

        users = load_users()
        stored = users.get(username)
        if not stored:
            return "User not found."

        # If it's an old user (string type), convert to dict automatically
        if isinstance(stored, str):
            stored = {"password": stored, "bio": "", "profile_pic": None}
            users[username] = stored
            save_users(users)

        if check_password_hash(stored["password"], password):
            session["username"] = username
            return redirect("/")
        else:
            return "Incorrect password."

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect("/")

@app.route("/upload_page")
def upload_page():
    if "username" not in session:
        return redirect("/login")
    return render_template("upload.html")

import uuid

from moviepy.editor import VideoFileClip

@app.route("/upload", methods=["POST"])
def upload():
    if "username" not in session:
        return "You must be logged in to upload.", 403

    video_id = str(uuid.uuid4())
    title = request.form.get("title")
    description = request.form.get("description", "")
    video_file = request.files.get("video")
    thumbnail_file = request.files.get("thumbnail")

    if not title or not video_file:
        return "Title and video file are required.", 400

    os.makedirs("temp", exist_ok=True)
    os.makedirs(VIDEO_FOLDER, exist_ok=True)
    os.makedirs(THUMB_FOLDER, exist_ok=True)

    # Save temporary upload
    temp_filename = f"{datetime.utcnow().strftime('%Y-%m-%d_%H-%M-%S')}_{secure_filename(video_file.filename)}"
    temp_path = os.path.join("temp", temp_filename)
    video_file.save(temp_path)

    # Check duration
    clip = VideoFileClip(temp_path)
    if clip.duration > 1.0:
        clip.close()
        os.remove(temp_path)
        return "Video is too long! Maximum length is 1 second.", 400
    clip.close()

    final_filename = temp_filename
    final_path = os.path.join(VIDEO_FOLDER, final_filename)
    square_path = final_path.replace(".mp4", "_square.mp4")

    try:
        # Crop to square and preserve audio (or add silent audio if missing)
        input_stream = ffmpeg.input(temp_path)
        video_stream = input_stream.video.filter(
            'crop', 'min(iw,ih)', 'min(iw,ih)', '(ow-iw)/-2', '(oh-ih)/-2'
        )
        audio_stream = input_stream.audio if input_stream.audio else ffmpeg.input('anullsrc=cl=stereo:r=44100').audio

        # Combine video and audio in one command
        ffmpeg.output(
            video_stream, audio_stream, square_path,
            vcodec='libx264', acodec='aac', audio_bitrate='128k',
            strict='experimental'
        ).overwrite_output().run(quiet=True)

        os.remove(temp_path)
        os.rename(square_path, final_path)

    except Exception as e:
        print("FFmpeg processing failed:", e)
        os.rename(temp_path, final_path)  # fallback to raw upload

    # Thumbnail: either user-provided or auto-generated
    if thumbnail_file:
        thumb_filename = secure_filename(thumbnail_file.filename)
        thumbnail_file.save(os.path.join(THUMB_FOLDER, thumb_filename))
    else:
        thumb_filename = final_filename.rsplit(".", 1)[0] + ".png"
        thumb_path = os.path.join(THUMB_FOLDER, thumb_filename)
        try:
            (
                ffmpeg
                .input(final_path, ss=0)
                .filter('scale', 320, -1)
                .output(thumb_path, vframes=1)
                .overwrite_output()
                .run(quiet=True)
            )
        except Exception as e:
            print("Thumbnail generation failed:", e)
            thumb_filename = None

    # Notify followers
    users = load_users()
    for follower in users[session["username"]].get("followers", []):
        follower_data = users.get(follower)
        follower_data.setdefault("notifications", []).append({
            "id": str(uuid.uuid4()),
            "type": "upload",
            "from_user": session["username"],
            "video_id": video_id,
            "video_title": title,
            "timestamp": datetime.utcnow().isoformat(),
            "read": False
        })
    save_users(users)

    # Save video metadata
    videos = load_videos()
    videos.append({
        "id": video_id,
        "title": title,
        "description": description,
        "video": final_filename,
        "thumbnail": thumb_filename,
        "uploader": session["username"],
        "views": 0,
        "likes": 0,
        "dislikes": 0,
        "liked_by": [],
        "disliked_by": [],
        "uploaded_at": datetime.utcnow().isoformat(),
        "comments": []
    })
    save_videos(videos)

    return redirect("/")

@app.route("/delete_video/<video_id>", methods=["POST"])
def delete_video(video_id):
    if "username" not in session:
        return "You must be logged in to delete videos.", 403

    videos = load_videos()
    video = next((v for v in videos if v["id"] == video_id), None)
    if not video:
        return "Video not found", 404

    if video["uploader"] != session["username"]:
        return "You are not allowed to delete this video.", 403

    # delete files
    video_path = os.path.join(VIDEO_FOLDER, video["video"])
    if os.path.exists(video_path):
        os.remove(video_path)

    # If thumbnail exists, delete it
    if video.get("thumbnail"):
        thumb_path = os.path.join(THUMB_FOLDER, video["thumbnail"])
        if os.path.exists(thumb_path):
            os.remove(thumb_path)

    # remove video from list and save
    videos = [v for v in videos if v["id"] != video_id]
    save_videos(videos)

    return redirect(url_for("index"))

@app.route("/edit_video/<video_id>", methods=["GET", "POST"])
def edit_video(video_id):
    if "username" not in session:
        return redirect(url_for("login"))

    videos = load_videos()
    video = next((v for v in videos if v["id"] == video_id), None)
    if not video:
        return "Video not found", 404

    if video["uploader"] != session["username"]:
        return "You are not allowed to edit this video.", 403

    if request.method == "POST":
        title = request.form.get("title")
        description = request.form.get("description", "")

        if not title:
            return "Title cannot be empty.", 400

        # update video
        video["title"] = title
        video["description"] = description
        save_videos(videos)
        return redirect(url_for("video_page", video_id=video_id))

    return render_template("edit_video.html", video=video)

@app.route("/like/<video_id>", methods=["POST"])
def like_video(video_id):
    if "username" not in session:
        return jsonify({"error": "Not logged in"}), 403

    username = session["username"]
    videos = load_videos()
    video = next((v for v in videos if v["id"] == video_id), None)
    if not video:
        return jsonify({"error": "Video not found"}), 404

    # Toggle logic
    if username in video.get("liked_by", []):
        video["liked_by"].remove(username)  # unlike
    else:
        video.setdefault("liked_by", []).append(username)
        if username in video.get("disliked_by", []):
            video["disliked_by"].remove(username)  # remove dislike if any

    video["likes"] = len(video.get("liked_by", []))
    video["dislikes"] = len(video.get("disliked_by", []))
    save_videos(videos)

    return jsonify({
        "likes": video["likes"],
        "dislikes": video["dislikes"],
        "following_like": username in video.get("liked_by", []),
        "following_dislike": username in video.get("disliked_by", [])
    })

@app.route("/dislike/<video_id>", methods=["POST"])
def dislike_video(video_id):
    if "username" not in session:
        return jsonify({"error": "Not logged in"}), 403

    username = session["username"]
    videos = load_videos()
    video = next((v for v in videos if v["id"] == video_id), None)
    if not video:
        return jsonify({"error": "Video not found"}), 404

    if username in video.get("disliked_by", []):
        video["disliked_by"].remove(username)  # undislike
    else:
        video.setdefault("disliked_by", []).append(username)
        if username in video.get("liked_by", []):
            video["liked_by"].remove(username)  # remove like if any

    video["likes"] = len(video.get("liked_by", []))
    video["dislikes"] = len(video.get("disliked_by", []))
    save_videos(videos)

    return jsonify({
        "likes": video["likes"],
        "dislikes": video["dislikes"],
        "following_like": username in video.get("liked_by", []),
        "following_dislike": username in video.get("disliked_by", [])
    })

@app.route("/comment/<video_id>", methods=["POST"])
def post_comment(video_id):
    if "username" not in session:
        return jsonify({"error": "Login required"}), 403

    text = request.form.get("text")
    parent_id = request.form.get("parent_id")  # optional, for replies

    if not text:
        return jsonify({"error": "Comment cannot be empty"}), 400

    videos = load_videos()
    video = next((v for v in videos if v["id"] == video_id), None)
    if not video:
        return jsonify({"error": "Video not found"}), 404

    comment_id = str(uuid.uuid4())
    new_comment = {
        "id": comment_id,
        "author": session["username"],
        "text": text,
        "timestamp": datetime.utcnow().isoformat(),
        "likes": 0,
        "dislikes": 0,
        "liked_by": [],
        "disliked_by": [],
        "replies": []
    }

    if parent_id:
        # Find the parent comment and add reply
        def find_comment(comments, pid):
            for c in comments:
                if c["id"] == pid:
                    return c
                r = find_comment(c.get("replies", []), pid)
                if r:
                    return r
            return None

        parent = find_comment(video.get("comments", []), parent_id)
        if parent:
            parent.setdefault("replies", []).append(new_comment)
        else:
            return jsonify({"error": "Parent comment not found"}), 404
    else:
        video.setdefault("comments", []).append(new_comment)

    if video["uploader"] != session["username"]:
        users = load_users()
        uploader_data = users.get(video["uploader"])
        uploader_data.setdefault("notifications", [])
        uploader_data["notifications"].append({
            "id": str(uuid.uuid4()),
            "type": "comment",
            "from_user": session["username"],
            "video_id": video["id"],
            "video_title": video["title"],
            "timestamp": datetime.utcnow().isoformat(),
            "read": False
        })
        save_users(users)

    save_videos(videos)
    return jsonify({"success": True, "comment": new_comment})

@app.route("/comment_like/<video_id>/<comment_id>", methods=["POST"])
def like_comment(video_id, comment_id):
    if "username" not in session:
        return jsonify({"error": "Login required"}), 403
    username = session["username"]

    videos = load_videos()
    video = next((v for v in videos if v["id"] == video_id), None)
    if not video:
        return jsonify({"error": "Video not found"}), 404

    def find_comment(comments, cid):
        for c in comments:
            if c["id"] == cid:
                return c
            r = find_comment(c.get("replies", []), cid)
            if r:
                return r
        return None

    comment = find_comment(video.get("comments", []), comment_id)
    if not comment:
        return jsonify({"error": "Comment not found"}), 404

    # Toggle like
    if username in comment.get("liked_by", []):
        comment["liked_by"].remove(username)
    else:
        comment.setdefault("liked_by", []).append(username)
        if username in comment.get("disliked_by", []):
            comment["disliked_by"].remove(username)

    comment["likes"] = len(comment.get("liked_by", []))
    comment["dislikes"] = len(comment.get("disliked_by", []))
    save_videos(videos)

    return jsonify({
        "likes": comment["likes"],
        "dislikes": comment["dislikes"],
        "following_like": username in comment.get("liked_by", []),
        "following_dislike": username in comment.get("disliked_by", [])
    })

@app.route("/comment_dislike/<video_id>/<comment_id>", methods=["POST"])
def dislike_comment(video_id, comment_id):
    if "username" not in session:
        return jsonify({"error": "Login required"}), 403
    username = session["username"]

    videos = load_videos()
    video = next((v for v in videos if v["id"] == video_id), None)
    if not video:
        return jsonify({"error": "Video not found"}), 404

    def find_comment(comments, cid):
        for c in comments:
            if c["id"] == cid:
                return c
            r = find_comment(c.get("replies", []), cid)
            if r:
                return r
        return None

    comment = find_comment(video.get("comments", []), comment_id)
    if not comment:
        return jsonify({"error": "Comment not found"}), 404

    # Toggle dislike
    if username in comment.get("disliked_by", []):
        comment["disliked_by"].remove(username)
    else:
        comment.setdefault("disliked_by", []).append(username)
        if username in comment.get("liked_by", []):
            comment["liked_by"].remove(username)

    comment["likes"] = len(comment.get("liked_by", []))
    comment["dislikes"] = len(comment.get("disliked_by", []))
    save_videos(videos)

    return jsonify({
        "likes": comment["likes"],
        "dislikes": comment["dislikes"],
        "following_like": username in comment.get("liked_by", []),
        "following_dislike": username in comment.get("disliked_by", [])
    })

@app.route("/delete_comment/<video_id>/<comment_id>", methods=["POST"])
def delete_comment(video_id, comment_id):
    if "username" not in session:
        return jsonify({"error": "Login required"}), 403
    username = session["username"]

    videos = load_videos()
    video = next((v for v in videos if v["id"] == video_id), None)
    if not video:
        return jsonify({"error": "Video not found"}), 404

    def delete_comment_recursive(comments, cid):
        for i, c in enumerate(comments):
            if c["id"] == cid:
                if c["author"] != username:
                    return False  # only author can delete
                del comments[i]
                return True
            if delete_comment_recursive(c.get("replies", []), cid):
                return True
        return False

    success = delete_comment_recursive(video.get("comments", []), comment_id)
    if not success:
        return jsonify({"error": "Comment not found or permission denied"}), 404

    save_videos(videos)
    return jsonify({"success": True})

@app.route("/user/<username>")
def user_profile(username):
    users = load_users()
    ensure_user_fields(users)
    user_data = users.get(username)
    if not user_data:
        return "User not found", 404

    # block non-admins from finding shadowbanned user pages except when owner views
    if user_data.get("shadowbanned", False) and not (is_admin(session.get("username")) or session.get("username")==username):
        return "User not found", 404

    videos = load_videos()
    user_videos = sorted([v for v in videos if v.get("uploader", "").lower() == username.lower()], key=lambda v: v.get("uploaded_at", 0), reverse=True)

    for v in user_videos:
        v["uploaded_ago"] = time_since(v.get("uploaded_at", datetime.utcnow()))

    logged_in = "username" in session
    session_username = session.get("username")

    return render_template(
        "profile.html",
        username=username,
        user=user_data,
        videos=user_videos,
        not_found=len(user_videos) == 0,
        logged_in=logged_in,
        session_username=session_username
    )

@app.route("/edit_profile", methods=["GET", "POST"])
def edit_profile():
    if "username" not in session:
        return redirect(url_for("login"))

    users = load_users()
    current_username = session["username"]
    user_data = users.get(current_username)

    if not user_data:
        return "User not found.", 404

    if request.method == "POST":
        new_username = request.form.get("username").strip()
        bio = request.form.get("bio", "")
        profile_pic_file = request.files.get("profile_pic")

        # Change username if different
        if new_username and new_username != current_username:
            if new_username in users:
                return "Username already taken.", 400

            users[new_username] = user_data  # copy existing data
            del users[current_username]
            session["username"] = new_username
            current_username = new_username

        # Update bio
        users[current_username]["bio"] = bio

        # Update profile picture
        if profile_pic_file:
            filename = secure_filename(profile_pic_file.filename)
            profile_pic_file.save(os.path.join("static/profile_pics", filename))
            users[current_username]["profile_pic"] = filename

        save_users(users)
        return redirect(url_for("user_profile", username=current_username))

    return render_template("edit_profile.html", user=user_data)

@app.route("/profiles")
def profiles():
    query = request.args.get("q", "").strip().lower()

    users = load_users()

    # Load videos
    with open("videos.json", "r") as f:
        videos = json.load(f)

    stats = {}
    now = datetime.utcnow()

    # --- Build uploader stats ---
    for v in videos:
        uploader = v["uploader"]
        if uploader not in stats:
            stats[uploader] = {"uploads": 0, "likes": 0, "last_upload": None}
        stats[uploader]["uploads"] += 1
        stats[uploader]["likes"] += v.get("likes", 0)

        uploaded_at = v.get("uploaded_at")
        if isinstance(uploaded_at, str):
            try:
                uploaded_dt = datetime.fromisoformat(uploaded_at)
            except ValueError:
                uploaded_dt = None
            if uploaded_dt and (
                stats[uploader]["last_upload"] is None or uploaded_dt > stats[uploader]["last_upload"]
            ):
                stats[uploader]["last_upload"] = uploaded_dt

    # --- Build user list ---
    user_list = []
    for username, data in users.items():
        is_shadowbanned = data.get("shadowbanned", False)
        uploads = stats.get(username, {}).get("uploads", 0)
        likes = stats.get(username, {}).get("likes", 0)
        last_upload = stats.get(username, {}).get("last_upload")

        # Skip users with no uploads
        if not last_upload:
            continue

        # Calculate inactivity (days since last upload)
        days_since_upload = (now - last_upload).days if last_upload else 9999
        if days_since_upload > 60:
            continue  # hide inactive users

        # Recency score (the more recent, the higher)
        recency_score = 100 / max(days_since_upload, 1)

        # Popularity formula
        popularity = (likes * 3) + (uploads * 2) + recency_score

        if not is_shadowbanned:
            user_list.append({
                "username": username,
                "bio": data.get("bio", ""),
                "profile_pic": data.get("profile_pic", None),
                "uploads": uploads,
                "likes": likes,
                "last_upload": last_upload,
                "popularity": round(popularity, 2),
                "days_since_upload": days_since_upload
            })

    # --- Filter and sort ---
    if query:
        user_list = [u for u in user_list if query in u["username"].lower()]

    user_list.sort(key=lambda u: u["popularity"], reverse=True)

    # --- Always return something ---
    if not user_list:
        return render_template("profiles.html", users=[], search_query=query, message="No active profiles found.")

    return render_template("profiles.html", users=user_list, search_query=query)

@app.route("/follow/<username>", methods=["POST"])
def toggle_follow(username):
    if "username" not in session:
        return jsonify({"error": "Not logged in"}), 403

    current_user = session["username"]
    if current_user == username:
        return jsonify({"error": "Cannot follow yourself"}), 400

    users = load_users()
    ensure_user_fields(users)

    follower = users.get(current_user)
    target = users.get(username)

    if not target:
        return jsonify({"error": "User not found"}), 404

    # Toggle follow
    if current_user in target["followers"]:
        target["followers"].remove(current_user)
        follower["following"].remove(username)
        following = False
    else:
        target["followers"].append(current_user)
        follower["following"].append(username)
        following = True

    save_users(users)

    return jsonify({
        "following": following,
        "followers_count": len(target["followers"])
    })

@app.route("/notifications")
def notifications():
    if "username" not in session:
        return redirect(url_for("login"))

    users = load_users()
    user_data = users[session["username"]]
    notifications = sorted(user_data.get("notifications", []),
                           key=lambda n: n["timestamp"], reverse=True)
    
    # Map type to emoji
    emoji_map = {"like": "👍", "comment": "💬", "upload": "📤"}
    
    for n in notifications:
        n["emoji"] = emoji_map.get(n["type"], "🔔")

    unread_count = sum(1 for n in notifications if not n["read"])

    return render_template("notifications.html",
                           notifications=notifications,
                           unread_count=unread_count)

@app.context_processor
def inject_notifications():
    if "username" in session:
        users = load_users()
        user_data = users.get(session["username"], {})
        unread_count = sum(1 for n in user_data.get("notifications", []) if not n.get("read", False))
        return {"unread_count": unread_count}
    return {"unread_count": 0}

from werkzeug.security import generate_password_hash

@app.route("/recover_account", methods=["GET", "POST"])
def recover_account():
    code_generated = False
    recovery_code = ""
    username = ""

    if request.method == "POST":
        action = request.form.get("action")
        username = request.form.get("username").strip()
        users = load_users()
        user = users.get(username)

        if not user:
            return "User not found", 404

        if action == "generate_code":
            # Generate a 6-character alphanumeric code
            recovery_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            user["recovery_code"] = recovery_code
            save_users(users)
            code_generated = True

        elif action == "reset_password":
            code = request.form.get("recovery_code").strip()
            new_password = request.form.get("new_password").strip()

            if user.get("recovery_code") != code:
                return "Invalid recovery code", 400

            user["password"] = generate_password_hash(new_password)
            user.pop("recovery_code", None)
            save_users(users)
            return "Password reset successful! You can now log in."

    return render_template(
        "recover_account.html",
        code_generated=code_generated,
        recovery_code=recovery_code,
        username=username
    )

@app.route("/show_recovery_code/<username>")
def show_recovery_code(username):
    users = load_users()
    user = users.get(username)
    if not user or "recovery_code" not in user:
        return "No recovery code found for this user."

    code = user["recovery_code"]
    return render_template("show_recovery_code.html", username=username, code=code)

@app.route("/recover_username", methods=["GET", "POST"])
def recover_username():
    users = load_users()
    message = None
    error = None

    if request.method == "POST":
        hint = request.form.get("hint", "").strip().lower()
        # Find all users matching the hint
        matching_users = [u for u, data in users.items() if data.get("hint", "").lower() == hint]

        if matching_users:
            message = f"Your username(s): {', '.join(matching_users)}"
        else:
            error = "No username found with that hint."

    return render_template("recover_username.html", message=message, error=error)

@app.route("/generate_recovery_code/<username>")
def generate_recovery_code(username):
    users = load_users()
    user = users.get(username)

    if not user:
        return "User not found.", 404

    # Generate a random 6-digit code (or alphanumeric)
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    
    # Save it in the user data
    user['recovery_code'] = code
    save_users(users)

    return f"Recovery code generated: {code}"

@app.route("/delete_account", methods=["GET", "POST"])
def delete_account():
    if "username" not in session:
        return redirect(url_for("login"))

    username = session["username"]
    users = load_users()
    videos = load_videos()

    if request.method == "POST":
        confirm_text = request.form.get("confirm_text", "").strip()
        if confirm_text != "DELETE":
            return "You must type DELETE to confirm.", 400

        # Delete user's videos and associated files
        user_videos = [v for v in videos if v["uploader"] == username]
        for v in user_videos:
            video_path = os.path.join("static/videos", v["video"])
            if os.path.exists(video_path):
                os.remove(video_path)
            if v.get("thumbnail"):
                thumb_path = os.path.join("static/thumbnails", v["thumbnail"])
                if os.path.exists(thumb_path):
                    os.remove(thumb_path)
        # Remove videos from list
        videos = [v for v in videos if v["uploader"] != username]
        save_videos(videos)

        # Delete user's notifications from other users
        for u, data in users.items():
            if "notifications" in data:
                data["notifications"] = [n for n in data["notifications"] if n.get("from_user") != username]

        # Finally, delete user account
        users.pop(username, None)
        save_users(users)

        # Log out
        session.pop("username", None)
        return "Account and all associated data deleted successfully."

    return render_template("delete_account.html")


def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        username = session.get("username")
        if not username or not is_admin(username):
            # return JSON for XHR or simple abort for normal POST
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"error": "Admin required"}), 403
            abort(403)
        return f(*args, **kwargs)
    return wrapper

@app.route("/admin")
@require_admin
def admin_dashboard():
    # simple overview of users and videos (admin only)
    users = load_users()
    videos = load_videos()
    # show important stats; templates can list them
    return render_template("admin_dashboard.html", users=users, videos=videos)

@app.route("/admin/delete_video/<video_id>", methods=["POST"])
@require_admin
def admin_delete_video(video_id):
    videos = load_videos()
    video = next((v for v in videos if v["id"] == video_id), None)
    if not video:
        return jsonify({"error": "Video not found"}), 404

    # delete files
    video_path = os.path.join(VIDEO_FOLDER, video["video"])
    if os.path.exists(video_path):
        os.remove(video_path)

    if video.get("thumbnail"):
        thumb_path = os.path.join(THUMB_FOLDER, video["thumbnail"])
        if os.path.exists(thumb_path):
            os.remove(thumb_path)

    videos = [v for v in videos if v["id"] != video_id]
    save_videos(videos)
    return jsonify({"success": True})

@app.route("/admin/delete_user/<username_to_delete>", methods=["POST"])
@require_admin
def admin_delete_user(username_to_delete):
    users = load_users()
    if username_to_delete not in users:
        return jsonify({"error": "User not found"}), 404

    # delete user's videos
    videos = load_videos()
    to_delete_videos = [v for v in videos if v.get("uploader") == username_to_delete]
    for v in to_delete_videos:
        video_path = os.path.join(VIDEO_FOLDER, v["video"])
        if os.path.exists(video_path):
            os.remove(video_path)
        if v.get("thumbnail"):
            thumb_path = os.path.join(THUMB_FOLDER, v["thumbnail"])
            if os.path.exists(thumb_path):
                os.remove(thumb_path)

    videos = [v for v in videos if v.get("uploader") != username_to_delete]
    save_videos(videos)

    # remove user record
    del users[username_to_delete]
    save_users(users)
    return jsonify({"success": True})

@app.route("/admin/toggle_shadowban/<username_to_toggle>", methods=["POST"])
@require_admin
def admin_toggle_shadowban(username_to_toggle):
    users = load_users()
    if username_to_toggle not in users:
        return jsonify({"error": "User not found"}), 404

    users[username_to_toggle].setdefault("shadowbanned", False)
    users[username_to_toggle]["shadowbanned"] = not users[username_to_toggle]["shadowbanned"]
    save_users(users)

    return jsonify({"success": True, "shadowbanned": users[username_to_toggle]["shadowbanned"]})

@app.context_processor
def inject_helpers():
    return {
        "is_admin": lambda u: is_admin(u),
        "is_moderator": lambda u: is_moderator(u)
    }

if __name__ == "__main__":
    # app.run(debug=True)
    # Below is for when I am not testing
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)