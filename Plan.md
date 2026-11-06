# Plan

convert each led id e.g. 174 to binary and add parity to it
group bits into 3 chunks, so each color channel (r,g,b) has one bit assigned
for each group create an invert group

- https://en.wikipedia.org/wiki/Manchester_code


## led detection

- [x] detect led light blobs in image
- [ ] detect led light blobs in video stream
- [ ] keep light blobs over time and track their position
- [ ] get rgb color of each light blob (in middle always white)
- [ ] detect the flash of leds (highest brightness in a loop)
- [ ] detect led color change
