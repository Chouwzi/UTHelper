import keyring

SERVICE = "UTHElearningAlert_Test"
USER = "test_user_123"
PASS = "super_secret_password"

print("1. Đặt password vào Windows Credential Manager...")
keyring.set_password(SERVICE, USER, PASS)
print("Thành công.")

print("2. Lấy password ra từ hệ thống...")
retrieved = keyring.get_password(SERVICE, USER)
print(f"Lấy được: {retrieved}")

if retrieved == PASS:
    print("MATCH! Windows Keyring hoạt động hoàn hảo.")
else:
    print("FAIL! Không đúng mật khẩu.")

print("3. Xoá password để dọn dẹp...")
keyring.delete_password(SERVICE, USER)
print("Done.")
