---
layout: page
title: "Papers — statistics"
permalink: /musings/papers/statistics/
share-title: "Musings — Papers (statistics)"
---

# statistics

Items in this category:

{% assign items = site.musings | where: "type", "paper" | where: "category", "statistics" | sort: "title" %}

<ul>
{% for i in items %}
  <li><a href="{{ i.url | relative_url }}">{{ i.title }}</a></li>
{% endfor %}
</ul>
