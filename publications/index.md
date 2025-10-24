---
layout: page
title: "Publications"
permalink: /publications/
share-title: "Publications"
---

# Selected Publications

Below is a curated list of my most relevant works. Click a title to open its dedicated page.

{% assign sorted = site.publications | sort: 'title' %}

<div class="container-md">
  <div class="row">
    {% for pub in sorted %}
      <div class="col-md-6 mb-4">
        <div class="card h-100">
          <div class="card-body">
            <h3 class="card-title"><a href="{{ pub.url | relative_url }}">{{ pub.title }}</a></h3>
            {% if pub.doi %}
              <p class="card-subtitle mb-2 text-muted">DOI: {{ pub.doi }}</p>
            {% endif %}
            <p class="card-text">{{ pub.content | strip_html | truncatewords:40 }}</p>
          </div>
        </div>
      </div>
    {% endfor %}
  </div>
</div>

If you'd like, edit the files in `_publications/` to change titles, add metadata (for example `doi:` or `year:`), or update the content — the index will update automatically.
