async function loadSiteData() {
  const response = await fetch("./custom-ui/site-data.json", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load site data: ${response.status}`);
  }
  return response.json();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function isMeaningfulUrl(url) {
  return Boolean(url && url !== "#");
}

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value;
}

function formatCount(value, singularLabel, pluralLabel = singularLabel) {
  return `${value} ${value === 1 ? singularLabel : pluralLabel}`;
}

function renderStats(data) {
  const container = document.getElementById("stats-grid");
  const stats = [
    ["最新抓取", data.latest_crawl_time || "-"],
    ["数据日期", data.news_date || "-"],
    ["主题数量", String(data.total_topics || 0)],
    ["快照平台", String(data.total_snapshot_platforms || 0)],
  ];

  container.innerHTML = stats
    .map(
      ([label, value]) => `
        <article class="stat-card">
          <span class="stat-label">${escapeHtml(label)}</span>
          <span class="stat-value">${escapeHtml(value)}</span>
        </article>
      `
    )
    .join("");

  const topline = document.getElementById("topline-date");
  if (topline) {
    topline.textContent = `更新 ${data.news_date || "-"} ${data.latest_crawl_time || ""}`.trim();
  }

  const badges = [
    data.total_topics ? `${data.total_topics} 个主题` : null,
    data.total_snapshot_platforms ? `${data.total_snapshot_platforms} 个平台` : null,
    data.rss_enabled ? "RSS 已开启" : null,
  ].filter(Boolean);
  const badgeNode = document.getElementById("hero-badges");
  if (badgeNode) {
    badgeNode.innerHTML = badges.map((item) => `<span class="hero-badge">${escapeHtml(item)}</span>`).join("");
  }

  const summaryBits = [
    data.total_topics ? `${data.total_topics} 个追踪主题` : null,
    data.total_snapshot_platforms ? `${data.total_snapshot_platforms} 个快照平台` : null,
    data.rss_enabled ? "RSS 更新已合并展示" : null,
  ].filter(Boolean);
  setText("hero-summary", summaryBits.join(" · ") || "把当天值得追的主题、平台快照和 RSS 更新整理到一页里。");
}

function matchesSearch(item, search) {
  if (!search) return true;
  const haystack = [
    item.title,
    item.platform_name,
    item.feed_name,
    item.last_crawl_time,
    item.published_at,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(search);
}

function renderHeadlineItems(items) {
  return items
    .map(
      (item) => `
        <a
          class="headline-item${isMeaningfulUrl(item.url) ? "" : " is-disabled"}"
          href="${escapeHtml(item.url || "#")}"
          target="_blank"
          rel="noreferrer"
          ${isMeaningfulUrl(item.url) ? "" : 'aria-disabled="true" tabindex="-1"'}
        >
          <div class="headline-title">${escapeHtml(item.title)}</div>
          <div class="headline-meta">
            ${item.platform_name ? `<span>${escapeHtml(item.platform_name)}</span>` : ""}
            ${typeof item.rank === "number" ? `<span><strong>Rank #${item.rank}</strong></span>` : ""}
            ${item.last_crawl_time ? `<span>${escapeHtml(item.last_crawl_time)}</span>` : ""}
            ${item.crawl_count ? `<span>出现 ${item.crawl_count} 次</span>` : ""}
            ${item.is_fresh ? `<span class="fresh-tag">最新</span>` : ""}
          </div>
        </a>
      `
    )
    .join("");
}

function renderTopics(data, search) {
  const section = document.getElementById("topics-section");
  const container = document.getElementById("topic-grid");
  const topics = (data.topics || [])
    .map((topic) => ({
      ...topic,
      items: topic.items.filter((item) => matchesSearch(item, search)),
    }))
    .filter((topic) => topic.items.length > 0);

  if (topics.length === 0) {
    section.classList.add("hidden");
    container.innerHTML = "";
    setText("topics-meta", search ? "当前搜索下没有匹配主题" : "按关键词聚合");
    return 0;
  }

  section.classList.remove("hidden");
  setText("topics-meta", search ? `${formatCount(topics.length, "组")}匹配主题` : `${formatCount(topics.length, "组")}主题聚合`);

  container.innerHTML = topics
    .map(
      (topic) => `
        <article class="topic-card">
          <div class="card-head">
            <div>
              <h3 class="card-title">${escapeHtml(topic.label)}</h3>
            </div>
            <span class="pill">${topic.items.length} 条</span>
          </div>
          <div class="meta-row">
            ${(topic.terms || [])
              .slice(0, 6)
              .map((term) => `<span class="meta-chip">${escapeHtml(term)}</span>`)
              .join("")}
          </div>
          <div class="headline-list">
            ${renderHeadlineItems(topic.items)}
          </div>
        </article>
      `
    )
    .join("");

  return topics.reduce((sum, topic) => sum + topic.items.length, 0);
}

function renderSnapshot(data, search) {
  const sectionNode = document.getElementById("snapshot-section");
  const container = document.getElementById("snapshot-grid");
  const snapshot = (data.snapshot || [])
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => matchesSearch(item, search)),
    }))
    .filter((section) => section.items.length > 0);

  if (snapshot.length === 0) {
    sectionNode.classList.add("hidden");
    container.innerHTML = "";
    setText("snapshot-meta", search ? "当前搜索下没有匹配快照" : "平台即时榜单");
    return 0;
  }

  sectionNode.classList.remove("hidden");
  setText("snapshot-meta", search ? `${formatCount(snapshot.length, "个平台")}匹配快照` : `${formatCount(snapshot.length, "个平台")}即时榜单`);

  container.innerHTML = snapshot
    .map(
      (section) => `
        <article class="platform-card">
          <div class="card-head">
            <h3 class="card-title">${escapeHtml(section.platform_name)}</h3>
            <span class="pill">${section.items.length} 条</span>
          </div>
          <div class="headline-list">
            ${renderHeadlineItems(section.items)}
          </div>
        </article>
      `
    )
    .join("");

  return snapshot.reduce((sum, section) => sum + section.items.length, 0);
}

function renderRss(data, search) {
  const section = document.getElementById("rss-section");
  const container = document.getElementById("rss-list");
  const rssItems = (data.rss_items || []).filter((item) => matchesSearch(item, search));

  if (!data.rss_enabled || rssItems.length === 0) {
    section.classList.add("hidden");
    container.innerHTML = "";
    setText("rss-meta", search ? "当前搜索下没有 RSS 结果" : "订阅更新");
    return 0;
  }

  section.classList.remove("hidden");
  setText("rss-meta", `${formatCount(rssItems.length, "条")}订阅更新`);

  container.innerHTML = rssItems
    .map(
      (item) => `
        <a class="rss-card" href="${escapeHtml(item.url || "#")}" target="_blank" rel="noreferrer">
          <h3 class="rss-title">${escapeHtml(item.title)}</h3>
          <div class="rss-meta">
            ${item.feed_name ? `<span>${escapeHtml(item.feed_name)}</span>` : ""}
            ${item.published_at ? `<span>${escapeHtml(item.published_at)}</span>` : ""}
            ${item.last_crawl_time ? `<span>抓取 ${escapeHtml(item.last_crawl_time)}</span>` : ""}
          </div>
        </a>
      `
    )
    .join("");

  return rssItems.length;
}

function updateSearchMeta(search, totalMatches) {
  const node = document.getElementById("search-meta");
  if (!node) return;

  if (!search) {
    node.classList.add("hidden");
    node.textContent = "";
    return;
  }

  node.classList.remove("hidden");
  node.textContent = totalMatches > 0 ? `“${search}” 找到 ${totalMatches} 条结果` : `“${search}” 暂无匹配结果`;
}

function render(data, search = "") {
  const emptyState = document.getElementById("empty-state");

  if (data.empty) {
    emptyState.classList.remove("hidden");
    document.querySelectorAll(".section").forEach((section) => {
      if (section.id !== "empty-state") section.classList.add("hidden");
    });
    updateSearchMeta(search, 0);
    return;
  }

  emptyState.classList.add("hidden");
  renderStats(data);
  const topicMatches = renderTopics(data, search);
  const snapshotMatches = renderSnapshot(data, search);
  const rssMatches = renderRss(data, search);
  const totalMatches = topicMatches + snapshotMatches + rssMatches;

  updateSearchMeta(search, totalMatches);

  if (search && totalMatches === 0) {
    emptyState.classList.remove("hidden");
    emptyState.innerHTML = `
      <h2>没有找到相关内容</h2>
      <p>换个关键词试试，或者清空搜索查看全部内容。</p>
    `;
  }
}

async function main() {
  try {
    const data = await loadSiteData();
    document.title = data.title || document.title;
    const titleNode = document.getElementById("site-title");
    if (titleNode && data.title) titleNode.textContent = data.title;
    render(data);

    const input = document.getElementById("search-input");
    input.addEventListener("input", (event) => {
      render(data, event.target.value.trim().toLowerCase());
    });
  } catch (error) {
    console.error(error);
    const emptyState = document.getElementById("empty-state");
    emptyState.classList.remove("hidden");
    emptyState.innerHTML = `
      <h2>页面加载失败</h2>
      <p>未能读取 custom-ui 生成的数据文件，请稍后刷新或检查生成脚本日志。</p>
    `;
  }
}

main();
