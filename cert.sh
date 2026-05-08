cd /home/$PATH/script/ssl
openssl req -new \
  -key key.pem \
  -out cert.csr \
  -config san.cnf \
  -reqexts req_ext

openssl x509 -req \
  -in cert.csr \
  -CA myca.cert.pem \
  -CAkey myca.key.pem \
  -days 397 \
  -sha256 \
  -extfile san.cnf \
  -extensions req_ext \
  -out cert.pem

cat cert.pem myca.cert.pem > fullchain.pem
sudo pkill python3
python3 /home/$PATH/script/bus.py
