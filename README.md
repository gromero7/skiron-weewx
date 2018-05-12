# skiron-weewx
## This website is already in development
Skiron is a meteorological website and social network formed by users that post its observations in it. 

This is an extension in order to upload weather station data to skiron website using Weewx software.

### How to install

1. Use wee_extension utility:

```
wee_extension --install skiron-x-x-x.tgz
```

Inside weewx.conf you will see now the next lines:

```
[[Skiron]]
  cloud_key = replace_me
  enabled = false
  cloud_id = replace_me
```

Replace the "replace_me" texts with your cloud_key and cloud_id from skiron website, and set enabled to true.

2. Restart weewx service:
```
sudo service weewx restart
```


Enjoy!
