---
layout: page
title: "Books"
permalink: /musings/books/
share-title: "Musings — Books"
---

# Books

A list of items from the `musings` collection which are tagged as books.

{% assign books = site.musings | where: "type", "book" | sort: "title" %}

<div class="container-md">
  <div class="row">
    {% for book in books %}
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
