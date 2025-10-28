// -------------------- Utility --------------------
async function postData(url = '', data = {}) {
    const formData = new FormData();
    for (const key in data) {
        if (data[key] !== undefined) formData.append(key, data[key]);
    }
    const response = await fetch(url, { method: 'POST', body: formData });
    return response.json();
}

// -------------------- Video Like/Dislike --------------------
function setupVideoVotes(videoId) {
    const likeBtn = document.getElementById("likeBtn");
    const dislikeBtn = document.getElementById("dislikeBtn");

    likeBtn?.addEventListener("click", async () => {
        const res = await fetch(`/like/${videoId}`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) return alert(data.error);

        likeBtn.textContent = `👍 Like (${data.likes})`;
        dislikeBtn.textContent = `👎 Dislike (${data.dislikes})`;

        likeBtn.classList.toggle("active-like", data.following_like);
        dislikeBtn.classList.toggle("active-dislike", data.following_dislike);
    });

    dislikeBtn?.addEventListener("click", async () => {
        const res = await fetch(`/dislike/${videoId}`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) return alert(data.error);

        likeBtn.textContent = `👍 Like (${data.likes})`;
        dislikeBtn.textContent = `👎 Dislike (${data.dislikes})`;

        likeBtn.classList.toggle("active-like", data.following_like);
        dislikeBtn.classList.toggle("active-dislike", data.following_dislike);
    });
}