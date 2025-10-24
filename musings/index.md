---
layout: page
title: "Musings"
permalink: /musings/
share-title: "Musings"
---

A short collection of books and reflections. Click a title to read my brief note about each book.

{% assign sorted = site.musings | sort: 'title' %}

<div class="container-md">
  <div class="row">
    {% for book in sorted %}
      <div class="col-md-6 mb-4">
        <div class="card h-100">
          <div class="card-body">
            <h3 class="card-title"><a href="{{ book.url | relative_url }}">{{ book.title }}</a></h3>
            <p class="card-text">{{ book.content | strip_html | truncatewords:40 }}</p>
          </div>
        </div>
      </div>
    {% endfor %}
  </div>
</div>

Add more entries by creating files in the `_musings/` folder; the page will update automatically.
