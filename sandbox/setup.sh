#!/bin/sh
set -e

until wp db check 2>/dev/null; do
  sleep 2
done

if ! wp core is-installed 2>/dev/null; then
  wp core install \
    --url=http://localhost:8080 \
    --title=WooCommerce \
    --admin_user=admin \
    --admin_password=admin \
    --admin_email=admin@example.com \
    --skip-email
fi

wp plugin is-active woocommerce 2>/dev/null || wp plugin install woocommerce --activate
wp rewrite structure '/%postname%/' --hard

HTACCESS=/var/www/html/.htaccess
if [ -f "$HTACCESS" ] && ! grep -q HTTP_AUTHORIZATION "$HTACCESS"; then
  sed -i '/RewriteEngine On/a RewriteRule .* - [E=HTTP_AUTHORIZATION:%{HTTP:Authorization}]' "$HTACCESS"
fi

CONFIG=/setup/local-config.json
if [ ! -f "$CONFIG" ]; then
  wp eval '
$user_id = 1;
$consumer_key = "ck_" . wc_rand_hash();
$consumer_secret = "cs_" . wc_rand_hash();
global $wpdb;
$wpdb->insert(
  $wpdb->prefix . "woocommerce_api_keys",
  [
    "user_id" => $user_id,
    "description" => "sandbox",
    "consumer_key" => wc_api_hash($consumer_key),
    "consumer_secret" => $consumer_secret,
    "permissions" => "read_write",
    "truncated_key" => substr($consumer_key, -7),
  ],
  ["%d", "%s", "%s", "%s", "%s", "%s"]
);
file_put_contents(
  "/setup/local-config.json",
  json_encode(
    [
      "site_url" => "http://localhost:8080",
      "consumer_key" => $consumer_key,
      "consumer_secret" => $consumer_secret,
    ],
    JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES
  ) . "\n"
);
'
fi

echo "Sandbox ready: http://localhost:8080 (admin / admin)"
echo "Target config: sandbox/local-config.json"
