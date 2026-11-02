<?php
/**
 * Test harness: just enough of WordPress (and optionally ACF) to render the generated theme's
 * templates from the command line. Usage:
 *   F2C_STUB=/path/stub.json php wp_stub.php
 * stub.json: {"theme": "<theme dir>", "template": "front-page.php", "fields": {...}|absent,
 *             "option": {...}, "rows": [{"acf_fc_layout": "...", ...}]|absent}
 */

$GLOBALS['stub'] = json_decode( file_get_contents( getenv( 'F2C_STUB' ) ), true );
define( 'ABSPATH', '/' );

function get_template_directory() { return $GLOBALS['stub']['theme']; }
function get_theme_file_uri( $p ) { return 'theme/' . $p; }
function home_url( $p = '/' ) { return 'http://site.test' . $p; }
function admin_url( $p = '' ) { return 'http://site.test/wp-admin/' . $p; }
function esc_html( $s ) { return htmlspecialchars( (string) $s, ENT_QUOTES, 'UTF-8' ); }
function esc_attr( $s ) { return htmlspecialchars( (string) $s, ENT_QUOTES, 'UTF-8' ); }
function esc_url( $s ) { return htmlspecialchars( (string) $s, ENT_QUOTES, 'UTF-8' ); }
function wp_kses_post( $s ) { return (string) $s; }
function __( $s ) { return $s; }
function esc_html__( $s ) { return $s; }
function esc_html_e( $s ) { echo esc_html( $s ); }
function get_option( $k ) { return isset( $GLOBALS['stub']['options'][ $k ] ) ? $GLOBALS['stub']['options'][ $k ] : false; }
function add_action() {}
function add_filter() {}
function language_attributes() { echo 'lang="en"'; }
function bloginfo( $k ) { echo 'UTF-8'; }
function wp_head() { echo '<link rel="stylesheet" href="theme/assets/css/main.css">'; }
function wp_body_open() {}
function wp_footer() {}
function body_class() { echo 'class="home"'; }
function get_header() { require get_template_directory() . '/header.php'; }
function get_footer() { require get_template_directory() . '/footer.php'; }
function get_template_part( $slug ) { require get_template_directory() . '/' . $slug . '.php'; }
function has_nav_menu( $l ) { return false; }
function wp_nonce_field() { echo '<input type="hidden" name="f2c_nonce" value="x">'; }
function have_posts() { return false; }
function wp_get_attachment_image( $id, $size, $icon, $attrs ) {
	$html = '<img src="uploads/' . (int) $id . '.jpg"';
	foreach ( $attrs as $k => $v ) { $html .= ' ' . $k . '="' . esc_attr( $v ) . '"'; }
	return $html . '>';
}

if ( isset( $GLOBALS['stub']['fields'] ) ) {
	$GLOBALS['stub_row'] = null;
	function get_field( $name, $post_id = false ) {
		if ( 'option' === $post_id ) { return isset( $GLOBALS['stub']['option'][ $name ] ) ? $GLOBALS['stub']['option'][ $name ] : null; }
		return isset( $GLOBALS['stub']['fields'][ $name ] ) ? $GLOBALS['stub']['fields'][ $name ] : null;
	}
	function get_sub_field( $name ) {
		$row = $GLOBALS['stub_row'];
		return ( $row && isset( $row[ $name ] ) ) ? $row[ $name ] : null;
	}
	function get_row_layout() { return $GLOBALS['stub_row'] ? $GLOBALS['stub_row']['acf_fc_layout'] : false; }
	function have_rows( $name ) {
		if ( ! isset( $GLOBALS['stub']['rows'] ) ) { return false; }
		if ( ! isset( $GLOBALS['stub_i'] ) ) { $GLOBALS['stub_i'] = 0; }
		if ( $GLOBALS['stub_i'] < count( $GLOBALS['stub']['rows'] ) ) { return true; }
		$GLOBALS['stub_row'] = null;
		return false;
	}
	function the_row() { $GLOBALS['stub_row'] = $GLOBALS['stub']['rows'][ $GLOBALS['stub_i']++ ]; }
}

require get_template_directory() . '/functions.php';
require get_template_directory() . '/' . $GLOBALS['stub']['template'];
