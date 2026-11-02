"""WordPress theme export: the generated site as a classic PHP theme with ACF fields.

The HTML the generator writes (and the design check verified) is turned into PHP templates without
changing the markup: every text, content image and button becomes an ACF field whose default is the
design's own content, so the theme renders exactly like the HTML before anyone edits anything, and
also without ACF installed.

Two flavours:
- ACF (free): one field group per page template (a tab per section), repeated cards as numbered
  fields, header/footer/shared sections edited once on the front page.
- ACF Pro: pages are built from Flexible Content sections (every section of the site is a layout,
  so sections can be reused or reordered on any page), repeated cards are Repeaters, and the
  header/footer/shared sections live on a "Site settings" options page.

Theme layout:
  style.css, functions.php, header.php, footer.php, front-page.php, index.php, page.php
  templates/page-<slug>.php            other pages (Template Name)
  template-parts/sections/<page>/...   one part per section
  template-parts/components/...        sections shared by several pages (edited once)
  inc/template-tags.php                field helpers (fall back to the design content)
  acf-json/                            field groups (ACF loads and syncs them)
  assets/css/main.css, assets/images/, assets/fonts/, screenshot.png, readme.txt
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from pathlib import Path

from .export import fold_corrections

VOID = {"img", "input", "br", "meta", "link", "hr", "source", "wbr"}
TEXT_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "span", "a", "li", "label", "button"}
INLINE = {"span", "br", "strong", "em", "b", "i", "a"}
INTERNAL = re.compile(r"^data-(figma-id|section|layout|fit|component)$")


# ----------------------------------------------------------------------------- tiny DOM
@dataclass
class Node:
    tag: str
    attrs: list[tuple[str, str | None]] = field(default_factory=list)
    children: list = field(default_factory=list)  # Node | str

    def get(self, name: str, default: str | None = None) -> str | None:
        return next((v for k, v in self.attrs if k == name), default)

    def set(self, name: str, value: str) -> None:
        for i, (k, _) in enumerate(self.attrs):
            if k == name:
                self.attrs[i] = (k, value)
                return
        self.attrs.append((name, value))

    @property
    def classes(self) -> list[str]:
        return (self.get("class") or "").split()

    def elements(self) -> list[Node]:
        return [c for c in self.children if isinstance(c, Node)]

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def walk(self):
        yield self
        for c in self.elements():
            yield from c.walk()


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        n = Node(tag, list(attrs))
        self.stack[-1].children.append(n)
        if tag not in VOID:
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, list(attrs)))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def handle_decl(self, decl):
        pass


def parse(html: str) -> Node:
    b = _Builder()
    b.feed(html)
    return b.root


def find(node: Node, tag: str) -> Node | None:
    return next((n for n in node.walk() if n.tag == tag), None)


def attrs_html(n: Node, skip: tuple[str, ...] = ()) -> str:
    out = []
    for k, v in n.attrs:
        if INTERNAL.match(k) or k in skip:
            continue
        out.append(f" {k}" if v is None else f' {k}="{escape(v, quote=True)}"')
    return "".join(out)


def inner_html(n: Node) -> str:
    """Inline content as HTML (texts, <br>, styled spans), without internal attributes."""
    out = []
    for c in n.children:
        if isinstance(c, str):
            out.append(escape(c, quote=False))
        elif c.tag in VOID:
            out.append(f"<{c.tag}{attrs_html(c)}>")
        else:
            out.append(f"<{c.tag}{attrs_html(c)}>{inner_html(c)}</{c.tag}>")
    return "".join(out)


# ----------------------------------------------------------------------------- PHP helpers
def php(value) -> str:
    """A Python value as a PHP literal (strings, numbers, lists and dicts)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    if isinstance(value, dict):
        return "array( " + ", ".join(f"{php(k)} => {php(v)}" for k, v in value.items()) + " )"
    if isinstance(value, (list, tuple)):
        return "array( " + ", ".join(php(v) for v in value) + " )"
    raise TypeError(type(value))


def php_pretty(value, depth: int) -> str:
    """Multi-line PHP array literal (WordPress coding standards style)."""
    ind = "\t" * depth
    if isinstance(value, dict) and value:
        width = max(len(php(k)) for k in value)
        rows = [f"{ind}\t{php(k).ljust(width)} => {php_pretty(v, depth + 1)}," for k, v in value.items()]
        return "array(\n" + "\n".join(rows) + f"\n{ind})"
    if isinstance(value, list) and value:
        rows = [f"{ind}\t{php_pretty(v, depth + 1)}," for v in value]
        return "array(\n" + "\n".join(rows) + f"\n{ind})"
    return php(value)


def key(*parts: str) -> str:
    return hashlib.sha1("/".join(parts).encode()).hexdigest()[:13]


def field_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "field"


def label(name: str) -> str:
    return re.sub(r"[_-]+", " ", name).strip().capitalize()


# ----------------------------------------------------------------------------- fields
@dataclass
class Field:
    name: str
    label: str
    kind: str  # text | textarea | image | link | repeater | tab | message
    default: object = None
    sub: list[Field] = field(default_factory=list)  # repeater sub fields
    count: int = 0  # repeater: number of designed items

    def acf(self, scope_key: str, pro: bool) -> list[dict]:
        """ACF field definition(s). Free has no Repeater: items become numbered fields."""
        base = {"key": f"field_{key(scope_key, self.name)}", "label": self.label, "name": self.name}
        if self.kind == "tab":
            return [{**base, "name": "", "type": "tab", "placement": "top"}]
        if self.kind == "text":
            return [{**base, "type": "text", "default_value": self.default or ""}]
        if self.kind == "textarea":
            plain = re.sub(r"<br\s*/?>", "\n", str(self.default or ""))
            return [{**base, "type": "textarea", "new_lines": "br", "rows": 3, "default_value": plain}]
        if self.kind == "image":
            return [{**base, "type": "image", "return_format": "array", "preview_size": "medium", "library": "all",
                     "instructions": "Leave empty to use the image from the design."}]  # fmt: skip
        if self.kind == "link":
            return [{**base, "type": "link", "return_format": "array"}]
        if self.kind == "repeater":
            if pro:
                subs = [f for s in self.sub for f in s.acf(f"{scope_key}/{self.name}", pro)]
                return [{**base, "type": "repeater", "layout": "block", "button_label": "Add item", "min": 0,
                         "sub_fields": subs,
                         "instructions": f"Leave empty to show the {self.count} items from the design."}]  # fmt: skip
            out = []
            for i in range(1, self.count + 1):
                for s in self.sub:
                    numbered = Field(f"{self.name}_{i}_{s.name}", f"{self.label} {i} · {s.label}", s.kind,
                                     (self.default or [{}] * self.count)[i - 1].get(s.name))  # fmt: skip
                    out += numbered.acf(scope_key, pro)
            return out
        raise ValueError(self.kind)


@dataclass
class Part:
    """One section as a template part."""

    slug: str  # file path below template-parts/, e.g. "sections/home/hero" or "components/site-footer"
    layout: str  # Flexible Content layout name (ACF Pro)
    title: str
    scope: str  # "page" or "global"
    php: str = ""
    fields: list[Field] = field(default_factory=list)


class Converter:
    """HTML (as generated and checked) → PHP template part + its fields."""

    def __init__(self, pages: dict[str, str], design_links: dict[str, str]):
        self.pages = pages  # html file -> page slug ("index.html" -> "home")
        self.design_links = design_links

    # --- helpers
    def url_expr(self, href: str) -> str:
        return f"f2c_url( {php(href)} )"

    def part(self, section: Node, slug: str, layout: str, title: str, scope: str, prefix: str) -> Part:
        self.names: set[str] = set()
        self.scope = scope
        self.prefix = field_name(prefix)
        p = Part(slug, layout, title, scope)
        self.fields = p.fields
        p.php = self.emit(section, 0, item=None)
        return p

    def unique(self, base: str, item: dict | None) -> str:
        names = item["names"] if item is not None else self.names
        name, n = base, 2
        while name in names:
            name, n = f"{base}_{n}", n + 1
        names.add(name)
        return name

    def slot_name(self, n: Node, kind: str, item: dict | None) -> str:
        cls = next((c for c in n.classes if "__" in c and not c.startswith("l-") and not c.startswith("btn")), "")
        role = re.sub(r"--\d+$", "", cls.split("__")[-1]) if cls else kind
        role = {"label": "text", "action": "button", "link": "link"}.get(role, role)
        if n.tag == "a" and "btn" in n.classes:
            role = "button"
        elif kind == "text":
            words = len(n.text().split())
            if (
                n.tag in ("h1", "h2")
                or (n.tag == "h3" and item is None)
                or n.tag == "h3"
                or (words <= 6 and item is not None and "title" not in (item["names"]))
            ):
                role = "title"
            elif words > 12:
                role = "description"
        base = field_name(role if item is not None else f"{self.prefix}_{role}")
        return self.unique(base, item)

    def get(self, name: str, default) -> str:
        scope = ", 'global'" if self.scope == "global" else ""
        return f"f2c_field( {php(name)}, {php(default)}{scope} )"

    # --- emit
    def emit(self, n: Node, depth: int, item: dict | None) -> str:
        ind = "\t" * depth
        if n.tag == "nav" and n.get("aria-label") == "Main":
            return self.emit_menu(n, depth)
        if n.tag == "img":
            return ind + self.emit_img(n, item)
        if n.tag in VOID:
            return ind + f"<{n.tag}{attrs_html(n)}>"
        if n.tag == "a":
            return ind + self.emit_link(n, item)
        if n.tag in TEXT_TAGS and self.inline_only(n) and n.text().strip() and n.tag != "button":
            return ind + self.emit_text(n, item)
        if n.tag == "form":
            return self.emit_form(n, depth, item)
        grid = next((c for c in n.classes if re.fullmatch(r"l-grid-\d+", c)), None)
        if grid and item is None and self.repeatable(n):
            return self.emit_repeater(n, depth)
        kids = []
        for c in n.children:
            if isinstance(c, str):
                if c.strip():
                    kids.append(ind + "\t" + escape(c.strip(), quote=False))
            else:
                kids.append(self.emit(c, depth + 1, item))
        if not kids:
            return f"{ind}<{n.tag}{attrs_html(n)}></{n.tag}>"
        return f"{ind}<{n.tag}{attrs_html(n)}>\n" + "\n".join(kids) + f"\n{ind}</{n.tag}>"

    @staticmethod
    def inline_only(n: Node) -> bool:
        return all(isinstance(c, str) or (c.tag in INLINE and Converter.inline_only(c)) for c in n.children)

    def emit_text(self, n: Node, item: dict | None) -> str:
        rich = any(isinstance(c, Node) for c in n.children)
        value = inner_html(n) if rich else n.text()
        kind = "textarea" if rich or len(value) > 90 else "text"
        name = self.slot_name(n, "text", item)
        cls_label = f"{label(name)}"
        if item is not None:
            item["fields"].append(Field(name, cls_label, kind))
            item["values"][name] = value
            expr = f"$item[{php(name)}]"
        else:
            self.fields.append(Field(name, cls_label, kind, value))
            expr = self.get(name, value)
        out = f"wp_kses_post( {expr} )" if kind == "textarea" else f"esc_html( {expr} )"
        return f"<{n.tag}{attrs_html(n)}><?php echo {out}; ?></{n.tag}>"

    def emit_img(self, n: Node, item: dict | None) -> str:
        src = n.get("src") or ""
        attrs = {k: v for k, v in n.attrs if not INTERNAL.match(k) and k != "src" and v is not None}
        if not re.match(r"images/img-", src):  # icons, illustrations, patterns: part of the design
            rest = attrs_html(n, skip=("src",))
            return f'<img src="<?php echo esc_url( f2c_asset( {php(src)} ) ); ?>"{rest}>'
        name = self.slot_name(n, "image", item)
        if item is not None:
            item["fields"].append(Field(name, label(name), "image"))
            item["values"][name] = src
            expr = f"$item[{php(name)}]"
        else:
            self.fields.append(Field(name, label(name), "image", src))
            expr = self.get(name, src)
        return f"<?php f2c_image( {expr}, {php(attrs)} ); ?>"

    def emit_link(self, n: Node, item: dict | None) -> str:
        href = n.get("href") or "#"
        label_node = next((c for c in n.elements() if c.tag == "span"), None)
        title = (label_node.text() if label_node is not None else n.text()).strip()
        name = self.slot_name(n, "link", item)
        default = {"url": href, "title": title}
        if item is not None:
            item["fields"].append(Field(name, label(name), "link"))
            item["values"][name] = default
            expr = f"f2c_link( $item[{php(name)}] )"
        else:
            self.fields.append(Field(name, label(name), "link", default))
            expr = f"f2c_link( {self.get(name, default)} )"
        var = "$f2c_link"
        inner = "<?php echo esc_html( " + var + "['title'] ); ?>"
        if label_node is not None:
            inner = f"<span{attrs_html(label_node)}>{inner}</span>"
        rest = attrs_html(n, skip=("href",))
        return (
            f"<?php {var} = {expr}; ?><a{rest} href=\"<?php echo esc_url( {var}['url'] ); ?>\""
            f"<?php echo f2c_target( {var} ); ?>>{inner}</a>"
        )

    def emit_menu(self, n: Node, depth: int) -> str:
        ind = "\t" * depth
        ul = next((c for c in n.elements() if c.tag == "ul"), None)
        if ul is None:
            return ind + f"<nav{attrs_html(n)}></nav>"
        links = [a for a in ul.walk() if a.tag == "a"]
        link_cls = links[0].get("class", "") if links else ""
        self.design_links.update({a.text().strip(): a.get("href") or "#" for a in links})
        fallback = []
        for a in links:
            href = a.get("href") or "#"
            url = f"<?php echo esc_url( f2c_url( {php(href)} ) ); ?>"
            fallback.append(
                f'{ind}\t\t\t<li><a{attrs_html(a, skip=("href",))} href="{url}">'
                f"{escape(a.text().strip(), quote=False)}</a></li>"
            )
        args = {
            "theme_location": "primary",
            "container": False,
            "menu_class": ul.get("class", ""),
            "items_wrap": '<ul class="%2$s" role="list">%3$s</ul>',
            "depth": 1,
            "f2c_link_class": link_cls,
        }
        return (
            f"{ind}<nav{attrs_html(n)}>\n"
            f"{ind}\t<?php if ( has_nav_menu( 'primary' ) ) : ?>\n"
            f"{ind}\t\t<?php wp_nav_menu( {php(args)} ); ?>\n"
            f"{ind}\t<?php else : ?>\n"
            f"{ind}\t\t<ul{attrs_html(ul)}>\n" + "\n".join(fallback) + f"\n{ind}\t\t</ul>\n"
            f"{ind}\t<?php endif; ?>\n{ind}</nav>"
        )

    def emit_form(self, n: Node, depth: int, item: dict | None) -> str:
        ind = "\t" * depth
        kids = [self.emit(c, depth + 1, item) for c in n.elements()]
        hidden = (
            f'{ind}\t<input type="hidden" name="action" value="f2c_contact">\n'
            f"{ind}\t<?php wp_nonce_field( 'f2c_contact', 'f2c_nonce' ); ?>\n"
            f'{ind}\t<?php if ( isset( $_GET[\'f2c_sent\'] ) ) : ?><p class="f2c-sent" role="status">'
            f"<?php esc_html_e( 'Thank you, your message was sent.', 'f2c' ); ?></p><?php endif; ?>"
        )
        rest = attrs_html(n, skip=("action", "method"))
        return (
            f'{ind}<form{rest} method="post" action="<?php echo esc_url( admin_url( \'admin-post.php\' ) ); ?>">\n'
            + hidden
            + "\n"
            + "\n".join(kids)
            + f"\n{ind}</form>"
        )

    # --- repeaters
    @staticmethod
    def signature(n: Node) -> tuple:
        return (n.tag, tuple(c for c in n.classes), tuple(Converter.signature(c) for c in n.elements()))

    def repeatable(self, n: Node) -> bool:
        items = n.elements()
        return len(items) >= 2 and len({self.signature(c) for c in items}) == 1

    def emit_repeater(self, n: Node, depth: int) -> str:
        ind = "\t" * depth
        items = n.elements()
        base = next((c for c in items[0].classes if "__" in c), "items")
        role = re.sub(r"--\d+$", "", base.split("__")[-1])
        name = self.unique(field_name(f"{self.prefix}_{role}s"), None)
        defaults, template, subs = [], "", []
        for i, it in enumerate(items):
            ctx = {"names": set(), "fields": [], "values": {}}
            body = self.emit(it, depth + 1, ctx)
            defaults.append(ctx["values"])
            if i == 0:
                template, subs = body, ctx["fields"]
        if not subs:  # nothing editable in the items (plain shapes): keep the markup as it is
            self.names.discard(name)
            kids = "\n".join(self.emit(c, depth + 1, None) for c in items)
            return f"{ind}<{n.tag}{attrs_html(n)}>\n{kids}\n{ind}</{n.tag}>"
        scope = ",\n" + ind + "\t\t'global'" if self.scope == "global" else ""
        self.fields.append(Field(name, label(name), "repeater", defaults, sub=subs, count=len(items)))
        var = "$" + name
        return (
            f"{ind}<{n.tag}{attrs_html(n)}>\n"
            f"{ind}\t<?php\n{ind}\t{var} = f2c_items(\n{ind}\t\t{php(name)},\n"
            f"{ind}\t\t{php_pretty(defaults, depth + 2)}{scope}\n{ind}\t);\n"
            f"{ind}\tforeach ( {var} as $item ) :\n{ind}\t\t?>\n"
            f"{template}\n"
            f"{ind}\t<?php endforeach; ?>\n"
            f"{ind}</{n.tag}>"
        )


# ----------------------------------------------------------------------------- theme files
TEMPLATE_TAGS = r"""<?php
/**
 * Field helpers. Every template reads its content through these, with the design's own content as
 * the default: the theme looks exactly like the design before anything is edited, and keeps working
 * if ACF is deactivated.
 *
 * @package __SLUG__
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/** Whether ACF is active. */
function f2c_acf() {
	return function_exists( 'get_field' );
}

/** Where content shared by every page (header, footer, shared sections) is stored. */
function f2c_global_id() {
	if ( F2C_ACF_PRO ) {
		return 'option';
	}
	$front = (int) get_option( 'page_on_front' );
	return $front ? $front : false;
}

/** Inside a Flexible Content section (ACF Pro page builder)? */
function f2c_in_row() {
	return F2C_ACF_PRO && function_exists( 'get_row_layout' ) && get_row_layout();
}

/** The stored value of a field, or null. */
function f2c_raw( $name, $scope = 'page' ) {
	if ( ! f2c_acf() ) {
		return null;
	}
	if ( 'global' === $scope ) {
		return get_field( $name, f2c_global_id() );
	}
	if ( f2c_in_row() ) {
		return get_sub_field( $name );
	}
	return get_field( $name );
}

/** True when a field was left empty. */
function f2c_empty( $value ) {
	return null === $value || false === $value || '' === $value || array() === $value;
}

/** A field's value, or the design's content when it is empty. */
function f2c_field( $name, $default = '', $scope = 'page' ) {
	$value = f2c_raw( $name, $scope );
	return f2c_empty( $value ) ? $default : $value;
}

/** Fills the empty values of one item with the design's content. */
function f2c_merge( $base, $row ) {
	foreach ( (array) $row as $key => $value ) {
		if ( ! f2c_empty( $value ) ) {
			$base[ $key ] = $value;
		}
	}
	return $base;
}

/**
 * Repeated items (cards, features, testimonials...). ACF Pro: a Repeater. ACF: numbered fields
 * (name_1_title, name_2_title...). Empty: the items from the design.
 */
function f2c_items( $name, $defaults, $scope = 'page' ) {
	if ( ! f2c_acf() ) {
		return $defaults;
	}
	$items = array();
	if ( F2C_ACF_PRO ) {
		$rows = f2c_raw( $name, $scope );
		if ( ! is_array( $rows ) || ! $rows ) {
			return $defaults;
		}
		foreach ( array_values( $rows ) as $i => $row ) {
			$base    = isset( $defaults[ $i ] ) ? $defaults[ $i ] : ( $defaults ? $defaults[0] : array() );
			$items[] = f2c_merge( $base, $row );
		}
		return $items;
	}
	foreach ( $defaults as $i => $base ) {
		$row = array();
		foreach ( array_keys( $base ) as $key ) {
			$row[ $key ] = f2c_raw( $name . '_' . ( $i + 1 ) . '_' . $key, $scope );
		}
		$items[] = f2c_merge( $base, $row );
	}
	return $items;
}

/** URL of a file in the theme's assets folder. */
function f2c_asset( $path ) {
	return get_theme_file_uri( 'assets/' . ltrim( $path, '/' ) );
}

/** Design links: "about.html" becomes the About page, "#contact" stays an anchor. */
function f2c_url( $href ) {
	if ( ! is_string( $href ) || '' === $href ) {
		return '#';
	}
	if ( preg_match( '#^(https?:|mailto:|tel:|/)#', $href ) || '#' === $href[0] ) {
		return $href;
	}
	if ( preg_match( '/^([a-z0-9-]+)\.html(#.*)?$/', $href, $m ) ) {
		$anchor = isset( $m[2] ) ? $m[2] : '';
		return ( 'index' === $m[1] ? home_url( '/' ) : home_url( '/' . $m[1] . '/' ) ) . $anchor;
	}
	return $href;
}

/** A link field (url, title, target), with design defaults. */
function f2c_link( $value ) {
	$link = array(
		'url'    => '#',
		'title'  => '',
		'target' => '',
	);
	foreach ( (array) $value as $key => $part ) {
		if ( is_string( $part ) && '' !== $part ) {
			$link[ $key ] = $part;
		}
	}
	$link['url'] = f2c_url( $link['url'] );
	return $link;
}

/** target/rel attributes for a link that opens in a new tab. */
function f2c_target( $link ) {
	return empty( $link['target'] ) ? '' : ' target="' . esc_attr( $link['target'] ) . '" rel="noopener"';
}

/** An image field (media library image with srcset) or the design's image. */
function f2c_image( $value, $attrs = array() ) {
	if ( is_array( $value ) && ! empty( $value['ID'] ) ) {
		if ( ! empty( $value['alt'] ) ) {
			$attrs['alt'] = $value['alt'];
		}
		unset( $attrs['width'], $attrs['height'] );
		echo wp_get_attachment_image( $value['ID'], 'full', false, $attrs ); // phpcs:ignore WordPress.Security.EscapeOutput
		return;
	}
	$html = '<img src="' . esc_url( is_string( $value ) ? f2c_asset( $value ) : '' ) . '"';
	foreach ( $attrs as $key => $attr ) {
		$html .= ' ' . esc_attr( $key ) . '="' . esc_attr( $attr ) . '"';
	}
	echo $html . '>'; // phpcs:ignore WordPress.Security.EscapeOutput
}

/** ACF Pro: renders the page's Flexible Content sections. False when the page has none. */
function f2c_sections() {
	if ( ! F2C_ACF_PRO || ! function_exists( 'have_rows' ) || ! have_rows( 'page_sections' ) ) {
		return false;
	}
	while ( have_rows( 'page_sections' ) ) {
		the_row();
		$layout = get_row_layout();
		if ( isset( $GLOBALS['f2c_layouts'][ $layout ] ) ) {
			get_template_part( 'template-parts/' . $GLOBALS['f2c_layouts'][ $layout ] );
		}
	}
	return true;
}
"""

FUNCTIONS = r"""<?php
/**
 * __NAME__ theme functions. Generated by Figma to Code from the Figma design.
 *
 * @package __SLUG__
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'F2C_VERSION', '1.0.0' );
define( 'F2C_ACF_PRO', __PRO__ );
define( 'F2C_SECTIONS_FIELD', '__SECTIONS_KEY__' );

require get_template_directory() . '/inc/template-tags.php';

/** Flexible Content layout => template part (every section of the design). */
$GLOBALS['f2c_layouts'] = __LAYOUTS__;

/** Pages of the design, created when the theme is activated. */
$GLOBALS['f2c_pages'] = __PAGES__;

/** Main menu items from the design. */
$GLOBALS['f2c_menu'] = __MENU__;

add_action(
	'after_setup_theme',
	function () {
		add_theme_support( 'title-tag' );
		add_theme_support( 'post-thumbnails' );
		add_theme_support( 'html5', array( 'search-form', 'gallery', 'caption', 'style', 'script' ) );
		register_nav_menus( array( 'primary' => __( 'Main menu', '__SLUG__' ) ) );
	}
);

add_action(
	'wp_enqueue_scripts',
	function () {
__FONTS__		wp_enqueue_style( '__SLUG__-main', get_theme_file_uri( 'assets/css/main.css' ), array(), F2C_VERSION );
	}
);

/* Menu links get the link class from the design. */
add_filter(
	'nav_menu_link_attributes',
	function ( $atts, $item, $args ) {
		if ( ! empty( $args->f2c_link_class ) ) {
			$atts['class'] = $args->f2c_link_class;
		}
		return $atts;
	},
	10,
	3
);

/* ACF field groups live in acf-json/ (versioned with the theme; ACF offers to sync them). */
add_filter(
	'acf/settings/load_json',
	function ( $paths ) {
		$paths[] = get_template_directory() . '/acf-json';
		return $paths;
	}
);
add_filter(
	'acf/settings/save_json',
	function () {
		return get_template_directory() . '/acf-json';
	}
);

if ( F2C_ACF_PRO ) {
	add_action(
		'acf/init',
		function () {
			if ( function_exists( 'acf_add_options_page' ) ) {
				acf_add_options_page(
					array(
						'page_title' => __( 'Site settings', '__SLUG__' ),
						'menu_slug'  => 'f2c-site-settings',
						'capability' => 'edit_theme_options',
						'icon_url'   => 'dashicons-admin-generic',
					)
				);
			}
		}
	);
}

/** Creates the design's pages and main menu once, when the theme is activated. */
function f2c_setup_content() {
	$front_id = 0;
	foreach ( $GLOBALS['f2c_pages'] as $page ) {
		$existing = get_page_by_path( $page['slug'] );
		$id       = $existing ? $existing->ID : wp_insert_post(
			array(
				'post_type'   => 'page',
				'post_status' => 'publish',
				'post_title'  => $page['title'],
				'post_name'   => $page['slug'],
			)
		);
		if ( ! $id || is_wp_error( $id ) ) {
			continue;
		}
		if ( $page['template'] ) {
			update_post_meta( $id, '_wp_page_template', $page['template'] );
		}
		if ( ! empty( $page['front'] ) ) {
			$front_id = $id;
		}
		if ( F2C_ACF_PRO && function_exists( 'update_field' ) && ! get_field( 'page_sections', $id ) ) {
			$rows = array();
			foreach ( $page['layouts'] as $layout ) {
				$rows[] = array( 'acf_fc_layout' => $layout );
			}
			update_field( F2C_SECTIONS_FIELD, $rows, $id );
		}
	}
	if ( $front_id ) {
		update_option( 'show_on_front', 'page' );
		update_option( 'page_on_front', $front_id );
	}
	if ( $GLOBALS['f2c_menu'] && ! has_nav_menu( 'primary' ) ) {
		$menu_id = wp_create_nav_menu( __( 'Main menu', '__SLUG__' ) );
		if ( ! is_wp_error( $menu_id ) ) {
			foreach ( $GLOBALS['f2c_menu'] as $i => $link ) {
				$url = f2c_url( $link['url'] );
				wp_update_nav_menu_item(
					$menu_id,
					0,
					array(
						'menu-item-title'    => $link['title'],
						'menu-item-url'      => ( '#' === substr( $url, 0, 1 ) ) ? home_url( '/' ) . $url : $url,
						'menu-item-status'   => 'publish',
						'menu-item-position' => $i + 1,
					)
				);
			}
			$locations            = get_theme_mod( 'nav_menu_locations', array() );
			$locations['primary'] = $menu_id;
			set_theme_mod( 'nav_menu_locations', $locations );
		}
	}
}
add_action( 'after_switch_theme', 'f2c_setup_content' );

/** Contact forms from the design: messages go to the site's admin email address. */
function f2c_handle_contact() {
	$nonce = isset( $_POST['f2c_nonce'] ) ? sanitize_key( wp_unslash( $_POST['f2c_nonce'] ) ) : '';
	if ( ! wp_verify_nonce( $nonce, 'f2c_contact' ) ) {
		wp_die( esc_html__( 'The form expired. Please go back and try again.', '__SLUG__' ) );
	}
	$lines = array();
	foreach ( $_POST as $key => $value ) {
		if ( in_array( $key, array( 'action', 'f2c_nonce', '_wp_http_referer' ), true ) || ! is_string( $value ) ) {
			continue;
		}
		$lines[] = sanitize_key( $key ) . ': ' . sanitize_textarea_field( wp_unslash( $value ) );
	}
	/* translators: %s: site name */
	wp_mail( get_option( 'admin_email' ), sprintf( __( 'New message from %s', '__SLUG__' ), get_bloginfo( 'name' ) ), implode( "\n", $lines ) );
	$back = wp_get_referer() ? wp_get_referer() : home_url( '/' );
	wp_safe_redirect( add_query_arg( 'f2c_sent', '1', $back ) );
	exit;
}
add_action( 'admin_post_nopriv_f2c_contact', 'f2c_handle_contact' );
add_action( 'admin_post_f2c_contact', 'f2c_handle_contact' );

/* Reminder when ACF is missing (the theme still shows the design's content). */
add_action(
	'admin_notices',
	function () {
		if ( function_exists( 'get_field' ) || ! current_user_can( 'activate_plugins' ) ) {
			return;
		}
		$plugin = F2C_ACF_PRO ? 'ACF Pro' : 'Advanced Custom Fields';
		/* translators: %s: plugin name */
		echo '<div class="notice notice-info"><p>' . esc_html( sprintf( __( 'Install and activate %s to edit the texts and images of this theme.', '__SLUG__' ), $plugin ) ) . '</p></div>';
	}
);
"""

README = """=== __NAME__ ===
Generated by Figma to Code from the Figma design. Requires at least: 6.0. Requires PHP: 7.4.

== Install ==
1. Appearance > Themes > Add New > Upload Theme, choose this zip, then Activate.
   The design's pages (__PAGE_LIST__) and the main menu are created automatically; the first page
   becomes the front page.
2. Install and activate __PLUGIN__ to edit texts, images and buttons.
   Field groups load from acf-json/ (ACF > Field Groups > Sync if it offers to).

== Editing ==
__EDITING__
Until a field is filled in, it shows the content from the design, so the site looks exactly like
the design from the start.

Menu: Appearance > Menus > "Main menu" (location: Main menu).
Contact forms send messages to the site's admin email (Settings > General).

== Files ==
front-page.php, templates/page-*.php   one template per page of the design
template-parts/sections/<page>/        each section of a page
template-parts/components/             sections shared by several pages (edited once)
inc/template-tags.php                  field helpers
assets/css/main.css                    all styles (tokens, layout, components, responsive)
"""


def _group(gid: str, title: str, fields: list[dict], location: list, order: int = 0) -> dict:
    return {
        "key": f"group_{gid}",
        "title": title,
        "fields": fields,
        "location": location,
        "menu_order": order,
        "position": "normal",
        "style": "default",
        "label_placement": "top",
        "instruction_placement": "label",
        "hide_on_screen": ["the_content"],
        "active": True,
        "description": "Generated by Figma to Code. Empty fields show the design's content.",
        "show_in_rest": 0,
    }


def theme_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "figma-theme"


def build_theme(out_dir: Path, conversion: dict, acf: str = "free", reference: Path | None = None) -> dict[str, bytes]:
    """The WordPress theme as {path inside the zip: bytes}. `acf` is "free" or "pro"."""
    pro = acf == "pro"
    pages = conversion.get("pages") or [{"name": "Home", "file": "index.html"}]
    name = (pages[0].get("frame") or pages[0].get("name") or "Figma theme").strip()
    slug = theme_slug(name)
    root = f"{slug}/"
    files: dict[str, bytes] = {}

    # Generated site, with the design check's corrections folded into the classes.
    htmls = {
        p["file"]: (out_dir / p["file"]).read_text(encoding="utf-8") for p in pages if (out_dir / p["file"]).exists()
    }
    corr = out_dir / "css" / "corrections.css"
    htmls, block = fold_corrections(htmls, corr.read_text(encoding="utf-8") if corr.exists() else "")
    css = (out_dir / "css" / "styles.css").read_text(encoding="utf-8") + block
    files[root + "assets/css/main.css"] = css.encode("utf-8")
    for sub in ("images", "fonts"):
        for f in sorted((out_dir / sub).glob("*")) if (out_dir / sub).exists() else []:
            files[root + f"assets/{sub}/{f.name}"] = f.read_bytes()

    page_slugs = {p["file"]: ("home" if p["file"] == "index.html" else Path(p["file"]).stem) for p in pages}
    conv = Converter(page_slugs, {})
    parts: dict[str, Part] = {}
    page_parts: dict[str, list[str]] = {}
    position: dict[tuple[str, str], str] = {}
    fonts_href = None
    for p in pages:
        if p["file"] not in htmls:
            continue
        tree = parse(htmls[p["file"]])
        link = next(
            (n for n in tree.walk() if n.tag == "link" and "fonts.googleapis.com" in (n.get("href") or "")), None
        )
        if link is not None:
            fonts_href = link.get("href")
        body = find(tree, "body")
        order = []
        seen_main = False
        placed = []  # (section, where): landmarks before or after <main>, or a section inside it
        for el in body.elements() if body is not None else []:
            if el.tag == "main":
                seen_main = True
                placed += [(sec, "main") for sec in el.elements()]
            elif el.tag not in ("script", "noscript"):
                placed.append((el, "foot" if seen_main else "head"))
        for sec, where in placed:
            comp = sec.get("data-component")
            sec_key = sec.get("data-section") or sec.get("id") or "section"
            if comp:
                part_slug, layout, scope, title = f"components/{comp}", field_name(comp), "global", label(comp)
            else:
                pslug = page_slugs[p["file"]]
                part_slug = f"sections/{pslug}/{sec_key}"
                layout, scope = field_name(f"{pslug}_{sec_key}"), "page"
                title = f"{p['name']} · {label(field_name(sec_key))}"
            if part_slug not in parts:
                parts[part_slug] = conv.part(sec, part_slug, layout, title, scope, comp or sec_key)
            order.append(part_slug)
            position[(p["file"], part_slug)] = where
        page_parts[p["file"]] = order

    # The site header / footer landmarks shared by every page go to header.php / footer.php.
    lists = [(f, o) for f, o in page_parts.items() if o]

    def shared(index: int, where: str) -> str | None:
        if not lists:
            return None
        cand = lists[0][1][index]
        ok = cand.startswith("components/") and all(o[index] == cand and position[(f, cand)] == where for f, o in lists)
        return cand if ok else None

    in_header, in_footer = shared(0, "head"), shared(-1, "foot")

    for part in parts.values():
        head = f"<?php\n/**\n * {part.title}.\n *\n * @package {slug}\n */\n\n?>\n"
        files[root + f"template-parts/{part.slug}.php"] = (head + part.php + "\n").encode("utf-8")

    def body(file: str, order: list[str]) -> str:
        def calls(where: str, indent: str) -> str:
            own = [s for s in order if position[(file, s)] == where and s not in (in_header, in_footer)]
            return "".join(f"{indent}get_template_part( 'template-parts/{s}' );\n" for s in own)

        return (
            f"get_header();\n{calls('head', '')}?>\n<main>\n<?php\n"
            f"if ( ! f2c_sections() ) {{\n{calls('main', chr(9))}}}\n?>\n</main>\n<?php\n{calls('foot', '')}get_footer();\n"
        )

    page_meta = []
    for p in pages:
        if p["file"] not in page_parts:
            continue
        pslug = page_slugs[p["file"]]
        order = page_parts[p["file"]]
        layouts = [parts[s].layout for s in order if position[(p["file"], s)] == "main"]
        if p["file"] == "index.html":
            files[root + "front-page.php"] = (
                f"<?php\n/**\n * Front page: {p['name']}.\n *\n * @package {slug}\n */\n\n" + body(p["file"], order)
            ).encode("utf-8")
            page_meta.append({"title": p["name"], "slug": pslug, "template": "", "front": True, "layouts": layouts})
        else:
            files[root + f"templates/page-{pslug}.php"] = (
                f"<?php\n/**\n * Template Name: {p['name']}\n *\n * @package {slug}\n */\n\n" + body(p["file"], order)
            ).encode("utf-8")
            page_meta.append({"title": p["name"], "slug": pslug, "template": f"templates/page-{pslug}.php",
                              "front": False, "layouts": layouts})  # fmt: skip

    header_part = f"<?php get_template_part( 'template-parts/{in_header}' ); ?>\n" if in_header else ""
    footer_part = f"<?php get_template_part( 'template-parts/{in_footer}' ); ?>\n" if in_footer else ""
    files[root + "header.php"] = (
        f"<?php\n/**\n * Document head and the start of the page.\n *\n * @package {slug}\n */\n\n?><!doctype html>\n"
        "<html <?php language_attributes(); ?>>\n<head>\n\t<meta charset=\"<?php bloginfo( 'charset' ); ?>\">\n"
        '\t<meta name="viewport" content="width=device-width, initial-scale=1">\n\t<?php wp_head(); ?>\n</head>\n'
        "<body <?php body_class(); ?>>\n<?php wp_body_open(); ?>\n" + header_part
    ).encode("utf-8")
    files[root + "footer.php"] = (
        f"<?php\n/**\n * The end of the page.\n *\n * @package {slug}\n */\n\n?>\n"
        + footer_part
        + "<?php wp_footer(); ?>\n</body>\n</html>\n"
    ).encode("utf-8")
    generic = (
        "get_header();\n?>\n<main>\n<?php\nif ( ! f2c_sections() ) {\n\twhile ( have_posts() ) {\n\t\tthe_post();\n"
        '\t\t?>\n\t\t<section class="container" style="padding-block: 64px;">\n'
        "\t\t\t<h1><?php the_title(); ?></h1>\n\t\t\t<?php the_content(); ?>\n\t\t</section>\n\t\t<?php\n\t}\n}\n"
        "?>\n</main>\n<?php\nget_footer();\n"
    )
    files[root + "page.php"] = (f"<?php\n/**\n * Any other page.\n *\n * @package {slug}\n */\n\n" + generic).encode()
    files[root + "index.php"] = (
        f"<?php\n/**\n * Fallback template.\n *\n * @package {slug}\n */\n\n" + generic
    ).encode()
    files[root + "inc/template-tags.php"] = TEMPLATE_TAGS.replace("__SLUG__", slug).encode("utf-8")

    # ACF field groups.
    sections_key = f"field_{key(slug, 'page_sections')}"
    groups: list[dict] = []
    global_fields = []
    for part in parts.values():
        if part.scope == "global" and part.fields:
            global_fields.append({"key": f"field_{key(slug, part.slug, 'tab')}", "label": part.title, "name": "",
                                  "type": "tab", "placement": "top"})  # fmt: skip
            global_fields += [f for fl in part.fields for f in fl.acf(f"{slug}/{part.slug}", pro)]
    if pro:
        layouts = {}
        in_main = {s for (_, s), where in position.items() if where == "main"}  # sections inside <main>
        for part in parts.values():
            if part.slug not in in_main:
                continue  # the site header / footer: in header.php / footer.php, not a page section
            lk = f"layout_{key(slug, part.layout)}"
            subs = [f for fl in part.fields for f in fl.acf(f"{slug}/{part.slug}/row", pro)] if part.scope == "page" else [
                {"key": f"field_{key(slug, part.slug, 'msg')}", "label": part.title, "name": "", "type": "message",
                 "message": "Shared by several pages: edit it once in Site settings."}]  # fmt: skip
            layouts[lk] = {"key": lk, "name": part.layout, "label": part.title, "display": "block",
                           "sub_fields": subs, "min": "", "max": ""}  # fmt: skip
        flexible = {"key": sections_key, "label": "Page sections", "name": "page_sections", "type": "flexible_content",
                    "button_label": "Add section", "layouts": layouts,
                    "instructions": "The page is built from these sections, in this order. Add, remove or reorder "
                                    "them; every section of the design can be used on any page."}  # fmt: skip
        groups.append(_group(key(slug, "sections"), "Page sections", [flexible],
                             [[{"param": "post_type", "operator": "==", "value": "page"}]]))  # fmt: skip
        if global_fields:
            groups.append(_group(key(slug, "site"), "Site settings", global_fields,
                                 [[{"param": "options_page", "operator": "==", "value": "f2c-site-settings"}]]))  # fmt: skip
    else:
        for p in page_meta:
            fields = []
            for s in page_parts[next(f for f, sl in page_slugs.items() if sl == p["slug"])]:
                part = parts[s]
                if part.scope != "page" or not part.fields:
                    continue
                fields.append({"key": f"field_{key(slug, part.slug, 'tab')}", "label": part.title, "name": "",
                               "type": "tab", "placement": "top"})  # fmt: skip
                fields += [f for fl in part.fields for f in fl.acf(f"{slug}/{part.slug}", pro)]
            loc = ({"param": "page_type", "operator": "==", "value": "front_page"} if p["front"] else
                   {"param": "page_template", "operator": "==", "value": p["template"]})  # fmt: skip
            if fields:
                groups.append(_group(key(slug, p["slug"]), f"{p['title']} page", fields, [[loc]]))
        if global_fields:
            groups.append(_group(key(slug, "site"), "Header, footer and shared sections", global_fields,
                                 [[{"param": "page_type", "operator": "==", "value": "front_page"}]], 1))  # fmt: skip
    for g in groups:
        files[root + f"acf-json/{g['key']}.json"] = json.dumps(g, indent=2, ensure_ascii=False).encode("utf-8")

    # functions.php
    fonts = (
        f"\t\twp_enqueue_style( '{slug}-fonts', {php(fonts_href)}, array(), null ); // phpcs:ignore\n"
        if fonts_href else ""
    )  # fmt: skip
    menu = [{"title": t, "url": u} for t, u in conv.design_links.items()]
    in_main = {s for (_, s), where in position.items() if where == "main"}
    layouts_map = {part.layout: part.slug for part in parts.values() if part.slug in in_main}
    files[root + "functions.php"] = (
        FUNCTIONS.replace("__NAME__", name)
        .replace("__SLUG__", slug)
        .replace("__PRO__", "true" if pro else "false")
        .replace("__SECTIONS_KEY__", sections_key)
        .replace("__LAYOUTS__", php(layouts_map))
        .replace("__PAGES__", php(page_meta))
        .replace("__MENU__", php(menu))
        .replace("__FONTS__", fonts)
    ).encode("utf-8")

    files[root + "style.css"] = (
        f'/*\nTheme Name: {name}\nAuthor: Figma to Code\nDescription: Generated from the Figma design "{name}". '
        f"Pixel-checked templates; texts, images and buttons are {'ACF Pro' if pro else 'ACF'} fields. "
        "Styles live in assets/css/main.css.\nVersion: 1.0.0\nRequires at least: 6.0\nRequires PHP: 7.4\n"
        f"Text Domain: {slug}\nLicense: GNU General Public License v2 or later\n"
        "License URI: https://www.gnu.org/licenses/gpl-2.0.html\n*/\n"
    ).encode()
    editing = (
        "ACF Pro: each page is built from sections (Pages > Edit > Page sections): edit them, reorder them,\n"
        "or add any section of the design to any page. Repeated cards are repeaters. The header, footer and\n"
        "sections shared by several pages are edited once in Site settings."
        if pro else
        "ACF: edit a page and use the tabs (one per section). Repeated cards are numbered fields. The header,\n"
        "footer and sections shared by several pages are edited once, on the front page."
    )  # fmt: skip
    files[root + "readme.txt"] = (
        README.replace("__NAME__", name)
        .replace("__PLUGIN__", "ACF Pro" if pro else "Advanced Custom Fields (free)")
        .replace("__PAGE_LIST__", ", ".join(p["title"] for p in page_meta))
        .replace("__EDITING__", editing)
    ).encode("utf-8")
    if reference is not None and reference.exists():
        files[root + "screenshot.png"] = _screenshot(reference)
    return files


def _screenshot(reference: Path) -> bytes:
    """WordPress theme screenshot (1200×900) from the top of the design."""
    from PIL import Image

    with Image.open(reference) as im:
        im = im.convert("RGB")
        h = min(im.height, round(im.width * 0.75))
        shot = im.crop((0, 0, im.width, h)).resize((1200, 900), Image.LANCZOS)
        buf = io.BytesIO()
        shot.save(buf, "PNG", optimize=True)
        return buf.getvalue()
