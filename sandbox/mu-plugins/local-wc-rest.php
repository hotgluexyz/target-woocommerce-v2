<?php
if (str_contains($_SERVER['REQUEST_URI'] ?? '', '/wp-json/wc/')) {
    $_SERVER['HTTPS'] = 'on';
}
$header = $_SERVER['HTTP_AUTHORIZATION'] ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '';
if (!isset($_SERVER['PHP_AUTH_USER']) && preg_match('/Basic\s+(.*)$/i', $header, $matches)) {
    $decoded = base64_decode($matches[1], true);
    if ($decoded !== false && str_contains($decoded, ':')) {
        [$user, $pass] = explode(':', $decoded, 2);
        $_SERVER['PHP_AUTH_USER'] = $user;
        $_SERVER['PHP_AUTH_PW'] = $pass;
    }
}
