---
layout: page
title: "Papers — astro-ph"
permalink: /musings/papers/astro-ph/
share-title: "Musings — Papers (astro-ph)"
---

# astro-ph

Items in this category:

{% assign items = site.musings | where: "type", "paper" | where: "category", "astro-ph" | sort: "title" %}

<ul>
{% for i in items %}
  <li><a href="{{ i.url | relative_url }}">{{ i.title }}</a></li>
{% endfor %}
</ul>
