from flask import Flask, request, jsonify
from flask_pymongo import PyMongo
from flask_bcrypt import Bcrypt
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from flask_cors import CORS
from datetime import datetime
import uuid
from oauthlib.oauth2 import WebApplicationClient
import requests
from dotenv import load_dotenv
import os

# Initialize app
app = Flask(__name__)
load_dotenv()
CORS(app)


mongo = PyMongo(app)
bcrypt = Bcrypt(app)
jwt = JWTManager(app)

app.config['MONGO_URI'] = os.getenv("MONGO_URI")
app.config['JWT_SECRET_KEY'] = os.getenv("JWT_SECRET_KEY")
client = WebApplicationClient(os.getenv("GITHUB_CLIENT_ID"))
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")
GITHUB_AUTHORIZATION_BASE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_API_URL = "https://api.github.com/user"

# Database Collections
users = mongo.db.users
posts = mongo.db.posts
follows = mongo.db.follows
likes = mongo.db.likes
comments = mongo.db.comments


# Helper Functions
def generate_uuid():
    return str(uuid.uuid4())


def validate_request(required_fields, data):
    missing_fields = [field for field in required_fields if field not in data]
    if missing_fields:
        return False, {"error": f"Missing fields: {', '.join(missing_fields)}"}
    return True, {}


# Error Handling
@app.errorhandler(404)
def not_found_error(_):
    return jsonify({"error": "Resource not found"}), 404


@app.errorhandler(500)
def internal_error(_):
    return jsonify({"error": "Internal server error"}), 500


# Routes
@app.route("/github/login")
def github_login():
    authorization_url = client.prepare_request_uri(
        GITHUB_AUTHORIZATION_BASE_URL, redirect_uri="http://localhost:5000/github/callback"
    )
    return jsonify({"auth_url": authorization_url})


@app.route("/github/callback")
def github_callback():
    code = request.args.get("code")
    token_url, headers, body = client.prepare_token_request(
        GITHUB_TOKEN_URL, client_id=GITHUB_CLIENT_ID, client_secret=GITHUB_CLIENT_SECRET, code=code
    )
    token_response = requests.post(token_url, headers=headers, data=body)
    client.parse_request_body_response(token_response.text)

    userinfo_url, headers, body = client.add_token(GITHUB_USER_API_URL)
    userinfo_response = requests.get(userinfo_url, headers=headers, data=body)
    user_info = userinfo_response.json()

    user = users.find_one({"github_id": user_info["id"]})

    if not user:
        user_id = generate_uuid()
        users.insert_one({
            "user_id": user_id,
            "username": user_info["login"],
            "github_id": user_info["id"],
            "profile_picture": user_info.get("avatar_url"),
            "created_at": datetime.now(datetime.timezone.utc)
        })
    else:
        user_id = user["user_id"]

    access_token = create_access_token(identity=user_id)
    return jsonify({"access_token": access_token})


@app.route("/user/profile", methods=["GET"])
@jwt_required()
def get_profile():
    user_id = get_jwt_identity()
    user = users.find_one({"user_id": user_id})

    if not user:
        return jsonify({"error": "User not found"}), 404

    user_posts = list(posts.find({"publisher_id": user_id}))
    for post in user_posts:
        post["_id"] = str(post["_id"])

    profile = {
        "username": user["username"],
        "profile_picture": user.get("profile_picture"),
        "posts": user_posts
    }
    return jsonify(profile)


@app.route("/post", methods=["POST"])
@jwt_required()
def create_post():
    data = request.json
    user_id = get_jwt_identity()

    required_fields = ["caption", "image_url"]
    is_valid, error_response = validate_request(required_fields, data)
    if not is_valid:
        return jsonify(error_response), 400

    post_id = generate_uuid()
    post = {
        "post_id": post_id,
        "caption": data["caption"],
        "image_url": data["image_url"],
        "music_url": data.get("music_url"),
        "category": data.get("category"),
        "datetime_posted": datetime.now(datetime.timezone.utc),
        "publisher_id": user_id
    }

    posts.insert_one(post)
    return jsonify({"message": "Post created successfully", "post_id": post_id}), 201


@app.route("/post/<post_id>", methods=["GET"])
def get_post_details(post_id):
    post = posts.find_one({"post_id": post_id})
    if not post:
        return jsonify({"error": "Post not found"}), 404

    post["_id"] = str(post["_id"])
    publisher = users.find_one({"user_id": post["publisher_id"]})

    post_details = {
        "caption": post["caption"],
        "image_url": post["image_url"],
        "music_url": post.get("music_url"),
        "category": post.get("category"),
        "datetime_posted": post["datetime_posted"],
        "publisher": {
            "username": publisher["username"],
            "profile_picture": publisher.get("profile_picture")
        },
        "likes_count": likes.count_documents({"post_id": post_id}),
        "comments_count": comments.count_documents({"post_id": post_id})
    }
    return jsonify(post_details)


@app.route("/follow", methods=["POST"])
@jwt_required()
def follow_user():
    data = request.json
    user_id = get_jwt_identity()

    required_fields = ["follow_user_id"]
    is_valid, error_response = validate_request(required_fields, data)
    if not is_valid:
        return jsonify(error_response), 400

    follow_user_id = data.get("follow_user_id")

    if not users.find_one({"user_id": follow_user_id}):
        return jsonify({"error": "User to follow does not exist"}), 404

    if follows.find_one({"follower_id": user_id, "following_id": follow_user_id}):
        return jsonify({"message": "Already following"})

    follows.insert_one({
        "follower_id": user_id,
        "following_id": follow_user_id,
        "followed_at": datetime.now(datetime.timezone.utc)
    })
    return jsonify({"message": "Successfully followed"})


@app.route("/feed", methods=["GET"])
@jwt_required()
def get_feed():
    user_id = get_jwt_identity()
    following = list(follows.find({"follower_id": user_id}))
    following_ids = [f["following_id"] for f in following]

    feed_posts = posts.find({"publisher_id": {"$in": following_ids}}).sort("datetime_posted", -1)

    paginated_posts = []
    for post in feed_posts:
        post["_id"] = str(post["_id"])
        paginated_posts.append(post)

    return jsonify(paginated_posts)


@app.route("/post/<post_id>/like", methods=["POST"])
@jwt_required()
def like_post(post_id):
    user_id = get_jwt_identity()

    if not posts.find_one({"post_id": post_id}):
        return jsonify({"error": "Post not found"}), 404

    if likes.find_one({"post_id": post_id, "user_id": user_id}):
        return jsonify({"message": "Post already liked"})

    likes.insert_one({
        "post_id": post_id,
        "user_id": user_id,
        "liked_at": datetime.now(datetime.timezone.utc)
    })
    return jsonify({"message": "Post liked successfully"})


@app.route("/post/<post_id>/like", methods=["DELETE"])
@jwt_required()
def unlike_post(post_id):
    user_id = get_jwt_identity()

    if not posts.find_one({"post_id": post_id}):
        return jsonify({"error": "Post not found"}), 404

    like = likes.find_one({"post_id": post_id, "user_id": user_id})
    if not like:
        return jsonify({"message": "Post not liked yet"})

    likes.delete_one({"post_id": post_id, "user_id": user_id})
    return jsonify({"message": "Post unliked successfully"})


@app.route("/post/<post_id>/comment", methods=["POST"])
@jwt_required()
def add_comment(post_id):
    user_id = get_jwt_identity()
    data = request.json

    required_fields = ["comment"]
    is_valid, error_response = validate_request(required_fields, data)
    if not is_valid:
        return jsonify(error_response), 400

    if not posts.find_one({"post_id": post_id}):
        return jsonify({"error": "Post not found"}), 404

    comment_id = generate_uuid()
    comment = {
        "comment_id": comment_id,
        "post_id": post_id,
        "user_id": user_id,
        "comment": data["comment"],
        "commented_at": datetime.now(datetime.timezone.utc)
    }
    comments.insert_one(comment)
    return jsonify({"message": "Comment added successfully", "comment_id": comment_id})
