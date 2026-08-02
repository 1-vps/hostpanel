<?php
declare(strict_types=1);

function fail_test(string $message): never
{
    fwrite(STDERR, $message . PHP_EOL);
    exit(1);
}

if ($argc !== 3) {
    fail_test('usage: php-runtime-compat.php HTTPS_URL CA_CERTIFICATE');
}

$url = $argv[1];
$caCertificate = $argv[2];
if (!str_starts_with($url, 'https://')) {
    fail_test('the PHP cURL compatibility probe requires HTTPS');
}
if (!is_file($caCertificate) || is_link($caCertificate)) {
    fail_test('the CA certificate is missing or unsafe');
}
if (!extension_loaded('curl') || !function_exists('curl_init')) {
    fail_test('php-curl is not loaded');
}

$curlDetails = curl_version();
$protocols = array_map('strtolower', $curlDetails['protocols'] ?? []);
if (!in_array('https', $protocols, true)) {
    fail_test('the installed PHP cURL binding lacks HTTPS support');
}

$handle = curl_init($url);
if ($handle === false) {
    fail_test('curl_init failed');
}
$configured = curl_setopt_array($handle, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_FOLLOWLOCATION => false,
    CURLOPT_CONNECTTIMEOUT => 3,
    CURLOPT_TIMEOUT => 5,
    CURLOPT_CAINFO => $caCertificate,
    CURLOPT_PROTOCOLS => CURLPROTO_HTTPS,
    CURLOPT_REDIR_PROTOCOLS => CURLPROTO_HTTPS,
    CURLOPT_USERAGENT => 'HostPanel-PHP-runtime-audit/1.0',
]);
if (!$configured) {
    curl_close($handle);
    fail_test('could not configure the PHP cURL handle');
}
$response = curl_exec($handle);
$status = (int) curl_getinfo($handle, CURLINFO_RESPONSE_CODE);
$error = curl_error($handle);
curl_close($handle);
if ($response === false) {
    fail_test('PHP cURL request failed: ' . $error);
}
if ($status !== 200 || trim((string) $response) !== 'hostpanel-php-curl-ok') {
    fail_test('PHP cURL returned an unexpected response');
}

$password = 'HostPanel PHP bcrypt compatibility ✓';
$hash = password_hash($password, PASSWORD_BCRYPT, ['cost' => 12]);
if (!is_string($hash) || !str_starts_with($hash, '$2y$12$')) {
    fail_test('PASSWORD_BCRYPT did not produce the reviewed format and cost');
}
if (!password_verify($password, $hash) || password_verify($password . 'x', $hash)) {
    fail_test('PHP bcrypt verification is inconsistent');
}
$info = password_get_info($hash);
if (($info['algoName'] ?? '') !== 'bcrypt' || (int) ($info['options']['cost'] ?? 0) !== 12) {
    fail_test('PHP bcrypt metadata is unexpected');
}

$boundedBcrypt = static function (string $value): string {
    if (strlen($value) > 72) {
        throw new LengthException('bcrypt input exceeds 72 bytes');
    }
    $result = password_hash($value, PASSWORD_BCRYPT, ['cost' => 12]);
    if (!is_string($result)) {
        throw new RuntimeException('bcrypt hashing failed');
    }
    return $result;
};
$boundedBcrypt(str_repeat('a', 72));
try {
    $boundedBcrypt(str_repeat('a', 73));
    fail_test('bcrypt accepted an input above the 72-byte boundary');
} catch (LengthException) {
    // Expected: callers must reject values that PHP bcrypt would truncate.
}

fwrite(STDOUT, "PHP cURL and bcrypt compatibility passed\n");
