---
layout: page
title: "Papers"
permalink: /musings/papers/
share-title: "Musings — Papers"
---

# Papers

<!-- Browse short papers and notes. Use the subject filters below to narrow down by area. -->

<p>
  <a class="btn btn-outline-secondary mr-2" href="{{ '/musings/papers/astro-ph/' | relative_url }}">astro-ph</a>
  <a class="btn btn-outline-secondary mr-2" href="{{ '/musings/papers/statistics/' | relative_url }}">statistics</a>
  <a class="btn btn-outline-secondary" href="{{ '/musings/papers/complexity/' | relative_url }}">complexity</a>
</p>

{% assign papers = site.musings | where: "type", "paper" | sort: "title" %}

<div class="container-md">
  <div class="row">
    {% for p in papers %}
      <div class="col-md-6 mb-4">
        <div class="card h-100">
          <div class="card-body">
            <h3 class="card-title"><a href="{{ p.url | relative_url }}">{{ p.title }}</a></h3>
            {% if p.category %}<p class="card-subtitle mb-2 text-muted">{{ p.category }}</p>{% endif %}
            <p class="card-text">{{ p.content | strip_html | truncatewords:40 }}</p>
          </div>
        </div>
      </div>
    {% endfor %}
  </div>
</div>
