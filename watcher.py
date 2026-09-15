# PM Edge background watcher
# Paper-alert only: this file never places a real-money order.

from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import sys
import time
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# ---------------- embedded PM Edge runtime ----------------
_RUNTIME = Path(".pmedge_watch_runtime")
_SENTINEL = _RUNTIME / ".ready"
_PAYLOAD = """UEsDBBQAAAAIAJagLl3oaEmW8QMAAKwMAAAWAAAAcG1lZGdlL21hcmtldF9wYXJzZS5webVXS2/jNhC++1cMFKCVEFn1Bt1ioyJ1tj31sO1h97BoHAiUNHbUSKRKUnadpv+9Q1IPS3aCbdL6INPz5sz3ifRaigqSZN3oRmKSQFHVQmpgnAvNdCG4mq2NSc40y0qmFKrOphfNWoHE2Wx23Yt9cnxAfvVJNhjMrAg+MHmP+mONWTwD+ugiu0cZg9LS/sYtcp1wVuEgq5kks6JmpGFWDI/wi+B4pE2PtJXNl+h9fRBQYYmZ2dtxMFFwHcO6FExPkihMMsHXRY48w9bE6iQy1Yai7ee4huSgJuVPthS4jZ/BT6KqBAdlWqeoEbpEqoxcmRZSxeC9h62CH73QrKicdnXdLba0iNrqtEbJFVzBjRXYsjw/Ol8GK3XuL+OtWkXLxy09rCA6D7zwhCXTz2mvx8pb+1wLafJDwfsy4t63oookRgqZzO580ocHAw6ph2yjrsjg56B3KdZQDQFcfwmaHKpoI0VT+2+CiLpY1H4Q9rKLXjY78DDTC90M3VzcFB0k/CqGvMh0APMfnkAlFU9RfUpC5p6TmdZ7QdBamZFNjEjkBUBNIbNRUapJTzh00tM+e1QJWYxdWmHyjJ9sSjRo8MCLfidI+zcH/laZ1LKgRux7/xCOTBQS4PNDo1sXPi1FaqM/9vFtMWG/ybArPXS1kOOE3uRvLSNVlwWljKmzb4KbxW23EYMEkhpgub5hqdAtbSgWginiKa61Cc/gE7IKNL3MSkiR0IqwQY6yyJzQMajSI6QS5m/ez39j84fF/BKir/zg62/mt44FYovSEGG+XOWGWquIvoOOaPYFopaPsuH03AhWKqtSGWXOaY+md8fIN3vVfaHDpqNS7FD6tv+Vjkcs0SdpMkDZd4gdU850LXS5EpeLWKRPUMu+3/xedRGQbBFdfhceFgoa/9TeQN6X1zBw1aR5u5iksQBIGw2i0Zkg9NDp5Oice+2kK/XFI9wVNBz6Tvcvm+ezY1Q1HQj5EyNU4xGq8QjP4KM9DFIh7oG4Z3pmT6k12wpZaIK/ACoe0j2YsuGzYZFpk0sK88/RyzHRFk54UCfwMG8BoSaAeNd7vgoMffITQOjCfwkICC8TGPx/fLXQdAGeYqyYcla8irUtWbxft/Yk6jgqBgIPQ/kPCDpJN2bnhJi2D6cmspvMY1eUpbtIdFxcLZfPNbraxS+r/+7izqB5dwLN3VYuvyU7w6g/GlSGa13ZlJfRJTiHdDTA7kA2mnbdD9zsnSL9ZQ5o0zUuvL9fMWxXfX+MdhW/M7MlKQw3h9d0Z0K3t6TYmNNZ0tm/4cUD5t8P12VouEQlyu0w3n+VkjjW1OYNh8dMX9iO0R8P6G26q7G7sFHGfwBQSwMEFAAAAAgA1p4uXdtNx6erBAAAywsAABEAAABwbWVkZ2UvbWF0Y2hlci5weZVWS4/bNhC++1cQykVKtIq9L2zdOuihBQoUCYqil8AwBFqi1oQlUiCptbRJ/ntnSOq19qapD7vUaB7fzHwzVKFkRdK0aEyjWJoSXtVSGUKFkIYaLoVeFKiSU0OzkmrNdK8ziJxGTc2h5Pv+7V/wuPDnTD85HUVrnhfN83OCf3pVhYFiYuSRiVQzk1rBwpkkjeHlEFNIVdGSP7PUsNbEEFRplnIN2otfB0AhWD4zsflHNSxaWBH5/YkJ85Ga7LBeEPgxfF6TnGeGfCWfpGBWnElR8JyJjK1JUUpqrFQxqqVYE20UBMpZQeBVngISCgUJMXX7kmxIgCjeG0ar/nUC6QcRufpgg21BLUbdncNRgw3WyjqJrIgXkKchdcJaro0OI6fpgECfBPnyzUpkY8DaP5y4OYCNrJkIBTuVXLBNEMQEcpE5F4+boDHF1QMgoZoUo8tCKqLkiXCBfUp+A4x/M5ozFRaTwPijEGzegBAMk0dmwsDmCtGCIIpmRtl3jDIqpOAZLS8ZQhUo8DAn2RyFT3xLd+A5W0yqAlLfHes4FLRiti0x8a1Yv2iB7QocXARxDhVdRNMYfU8Rv4iJiHxE23DNK15SxU0XUh94/wMALM8chDYmHaZl8dPBLIq9aD+KplRpCTQRD90ZVZbJstdsyWZzQWM11QAWdOisw1N7wdtP9wuHFKavQ/q1ia5LbkLA2PVHq/GG/AHtK2FhlHTPYIZ1kx2QfMEndiKfpTqSjwF5mj0zo4OkR1MCk1sTIWo8dnBEPvTiDxtybQWt2a6vVjubnTuOuDEpswXRdtkruIc5qcb0bhbTdle0De02CrEz0dmScuKIvCerJVbakSGtKVepzqRiZ0Q4yJGUJ9r9P3rkXLEMix6e8c15HulB3p1xcu9CjjqI+9qn+8Rgk77ieW512fM8+tTzUEiHPu6D9aNT4VJO7T4Odc2y2O1mKEYJ62+LFdm9WqEYzNUR2mG4r6vf5pAK/rMVfLn7/dRgsARuEMMzXlNhUtqP0dmb/dkkjD5DjBPjoMEWc2jIxFa7ZY4XVR5Ejl0ec46tHK6wcJKJ3zlUHFkOOtvdol/VDAfTF2jA5IsPbQJl5pYrylJslFuucS9HrYl88IGcBespd88LFF8oTTwJ/2I9+WJb179Ahe7u5mMHt63homGLQYrJu+iApd9M+LMpv6yYTymTVYVXti3d9B7hxaTUuCp6L3MYB9koDY7pXofhaHA1qEeJgc+hEuYeEOdwIyO/b+6Xy5mbN+RPWuoDJ1kpYZYQjMa9DZQtCqaI/Z458uwoi+Jn2CQlgx3C4JbLsXB7uMwVO1GVk1rJFobLdMnM/6w2OFDL5OYuxioB1IqL0OYRAzCEd/sQjWUdP2vAdJk83JO3rivv4Gl1C0+j85Hpln4JreGTIg/D0QdwKbbm8cQs8uH8cDnjHx0bIYFY8IGn91IeXdndXugnxmPRoBMeWbcpabXPKdxQpIVlPuwU/8mHFnumkSzOEHSmt4oTwhVCVpYWqIsXxFWv7u4LpOzy9r9z6M3fYmkhG8DGHxvZ6NeTuuzOOlrtBo8xKQIc1c0XK7jerZPr4purupfdOBm4/BdQSwMEFAAAAAgAo54uXQAAAAACAAAAAAAAABIAAABwbWVkZ2UvX19pbml0X18ucHkDAFBLAwQUAAAACAD2ni5d4DJm+RoCAAAPBQAADwAAAHBtZWRnZS9wYXBlci5webVUTW/bMAy9+1cQ6sVeEy85Llh6GXbdZaehCATDpmshsujpI0U39L+Pshw7brHdpoMBU4/vkXyEWks9SNkGHyxKCaofyHqojCFfeUXGZW2ElOdKu05d7w3ZvtLqF8q+smf0WZY12ILF1qLr5FANaCXZBq3LnSeLG0gEstYKjS9g+wCNqv0hAz6t0trBEXbjH1OQDqP4GEsQsjASgjIwUpY0oFlLFYkunlQX578tNV8VUj6hv16MJI/Cq/qMVpyKYiar3JmZEuxRvKCTHBEnUC1MSU41yIHjEcSPr98FoHY4JxhK+JnvDr5wc2gvPOILgqdQd9s4BFZyoR9i7wfwHQINAznlEX4G4m8fnAdvq4aTOkvhqfs4JgMFC1r1ypezCBcX61aOR+DhGxlkW5sx9vkIraZqbnnMlINVNTdRwD3scftpmWU8aeSxo2nksVxsrgyqEafNv0iLFVsy/P4I+7+6G8y4BxdsJqn/bTLLBR3JnLd5QsfEXKQLUXCFIERRanpGmxe3g55y46C5gd9xRcQG2Hjxuh5jTcYrE3AOxsWRz2RYN1/t0lUm7lSiXxSH6oVCLHVf7qL8TDKu3a7czcg4uuQBvPF8ueEpLHh8D0SU0fBYuq1qfwsfjGa4MjP4CnJxjz5APlW6vS1kG1UWjmT3ZHYyeb1VqftNFFu5NT8Syx5Z5HcsGrDsqBOHtG7sR4repPLdzd9r9gdQSwMEFAAAAAgAR58uXcU85LjNBAAAEQ0AABEAAABwbWVkZ2UvdHJhZGluZy5wea1WbU/kNhD+vr9i5Puy6YUV9Fu3XVRUVqcTx3IChFSdkOWNHdbdbJzaDmU53X/v+C3J5gCd1PIByMz48bw8M+NSqx1QWra21YJSkLtGaQusrpVlVqraTEpnwpllRcWMESbZdKJJMJltWWU2Mqkv/NdZazd/VFLUNo+SpdZK51ArvWOVfBZ0x/RW2Mlk8nuHOEXAZ1EvbnUrsokXwSf5KK40F/pzxer5BPDHymIr9ByM1f7bSC76r7VSW3ooMu16J60VnDZaFqgoK8WsVxWqtpoV1sxB1kHUKGOpqqv93GFV8cqdoLKmpdJFBJ5MuCjx7oeaVdQqqpyTtEEvp0E4By4Lm8HR6UtBOA9hEc9/Ie6T3If7nY+o8k5OkwGmUu+D/+Q+84ayjCgLIH8ub0gAPsgBwpC15KTTjFKBev/X60U1RFxdDQDfxSrC3c/wd6ss0kE8okvgozagarAbAeiFvxsz1+5l/QCrK2AWViANMLN1EmeCopOj1exld9HuLXdPZsdwdOC0Eb2fmkkj4I5VrfCEm5Ibn78Q1q41FtbBTaXROZJNQiYN0hE5NSiIeBJF6zqB3Pt0XJ5dLINfWmDT1Ic1nfYex/OBpOQ+91fnfYz5OKjcMa+rc0dIrHLeoSYHcyAPSnFqZVXRgtWFqAQnjgldCC4hQORuJ7hkViAvoyEJcFlkbghQ0MjgCsOJTlDJfTegp8JaLJrB/6zSIpsn3uGY6JQzd5SKmq3Rl3EpBs3f58j9EJc/wFC5YwXyw0EyjV7P4EZY+HxJl+cflvTy6ny5cDfgcOJADjGS0aePd0t6dn1Jb68ulquF+8+Lbq/Pzj+uPoDrZmClxfQ0rMHfjziDuB90sx4ykiEkwVHBxTx7EDbmqE9PdpCH0O1vBF4mFn7tEL75o6Vqa046tJ4H7tbpgAuZ4+txBr8t4Pitm9JFG+YSCqxptHoUvBtz6OyzIAeB0gLn3WjchOsFzkGnjLfPjvux4zOjpdl6Axx/nO2nGbw/wDztKbJm9VYr5CzHXwwHxk+9bseeKGeywumGDr6HE3H0y5sx3m6QLn7wwD+qrThSuRAYpJtAHgicZ1CwhrzqsZ9eh9E6CY1t6yMm5D9EFOD+h4jCnB1EFPaWX65YtvG+HUyi5E5Y0HTNjKCtrvLvNKyRdCv2SMv81dM4qh7dOHF2DbObNEy8M63WwZvxep8GP30TRcl4QGYBw++3H9l6G6Y5Yj19Z5sUg/UYQE/7Mz9QixvHFB6PhjoYDwAI8CtO/7I1bmT5meRrlmryDq7FX8I1mWWVgFvcGJE9xjmTsoQLzvfnzncmW+OfDn/WL/aX15ADHexlP3od4CLBfyF7YZwIDwxwwvuieyqELdEdqVU40e+bsodGaq5U7WLtZW+k9JW0Xvrqx6DXYq9wmr+Q1ZA66zdZSm5CTRRJXox2LmrcgyvyIRtpR1xCY/8dKudebd3uf/Exl8XVbxp8G7t3SCQ2mhQiNrs/1PdfIPjCgczC/4PuwnIETf8u6JQFbgUbtN0G6LU+gKAdPyR6m/SEDXbdZz5wbvCgTT4ORMP2DrOTce4fCTHMbpPl8LVDTU09h2HUENgXheENRLq4k/yFRJBRgB3E+AE12JTRZJS4b3lXu1RJ/4hLwsm/UEsDBBQAAAAIAMaeLl0+jqfXowIAAHwGAAAPAAAAcG1lZGdlL2RldmlnLnB5jVRNj9MwEL3nV4x6ciBNU8FKUG1XSIgrJ8SlqiI3mYBFYke2s4KKH8/4I06ySIicnOeZ996Mx+60GqCuu8lOGusaxDAqbYFLqSy3QkmTdS6k5ZY3PTcGzRyToCwCxmUYKxqTZdmHtM2I4I7y/EVPmGcego9EjNJM5pQBfR0Xuh61up2g6xW3HpT1Takf5gRCBqAVZkRtyNQ6rFGyEy3KBteoS/WMlG+nsceL3yugLMsr2WuxgxYbMfC+tqqmCnqBLZsh1bYm0uWwfwqr4FUsiS4KHs9wLKuw5z7NhUH4yvsJP2mtNNvFcPDhw2Qs3BCe4LjLfZJG6r10JHDYUEebUmlCxB1DPSx6PUFPzQ5lXb3J1X+wY+kMeziDmYY5K59rCHtk/p/WYxY4ZX4TvbACYw1ESiRUR7Wt4zJSGYG9UxpGOj6INEvjn8W3Wk22UQMaNi9iRa1oYkFudTFWF7Cu6pn3hoq6XMOUcMpcfp2kcpKJNFU3atEgRXoupi47D+yueYpwzCUfR5TLJKyGw8fnS7iXnuPJpuN0GFHmaaxJ8OUBOplNy1yd7C5G5hkLn0cUoVmUQu2um/nGsDjVq9MuYBDr20KabwjjP+u/rgxtVeWx8u1NlxB+w2clMbSq6ZFL19DQpzFfjtEru+Gp4BHS9iMcr/NU9SiZJ3DwYmqZsFCwU/PYQLN1Xj0cJQGCzxxhiLUYQhhNVrISXJIip4aOsHdMuRvnbdUbY5HpP63Fw9t44wtJepG2MaOxNN2L1EtpuvaAPd2yqqzCQ6WRt/Y7sZAnRo9AsU04wLuyCmok46/gL/BlssoFu2djH4wcPLptAAXg/n2cx+WpXBgeikW4Kt8+wKtk6TUBDw5IwvlmbNP8MNerre/CWypWkkV4h1NEnv0BUEsDBBQAAAAIALqeLl0Tt5wl5AgAACEcAAAQAAAAcG1lZGdlL2thbHNoaS5wea1ZbW/bOBL+nl9B6JPUc2Qnly0WvvUdgtabBn25oE4POASBQEt0zIssaUkqjevzf98ZvkiiLKfd2wsWNcV55cxw5pF2JcoNSZJVrWrBkoTwTVUKRWhRlIoqXhbyxG4tqWSvL9xTRhVTfMMIlSRTbvc/sixOVqiyomqd86XTdwOPhlCLHPbjigrJHBX29LNTU9c8c2vBfquZVNJIp2JbqfJB0Gq9jdf024aquBJ8wxV/YtLpW1O5ZnJEJBOc5vybPsiPKIip3G42TAmeOl0VzTJePJwY8bhWPG/sqDJZ5SVVJycnaU6lJO9pLtd8LkQpws91gQHSD9H0hMBfBTw93pt6mfP0Tc5ZoQxTxlaQEF5wlSShZPlqpEOfQJCmRCoxIqi2rNWU8EKRGTmfWPX4hwKx4weiW8YCRHkVBuMg8pmtNuC1K58smZQQPSC7TMQLsxVGg5zxmtGMCRnXFRZJuAu+QB5OLx/ghMGUBDcfT+fZAxufx5NgH520Z35gyp4Xi8eeFeqCbuSUZDxV5L/kU1kwcAV/InL6d73dHl6JbfuAfwJ4Pd+cjTZEf9HWnKGZ+SGlILt9E+lZN1CRbyEWlIOqVSkSCTemlmGPgcHNKoAP70aHxp5TVikSNlH9bBZzvQ++jsi/aF7b+sFrBhK906Flr+ZWgXlyuSIrynOWTckOhPdBRHQRw7oNe86lSsqKFcmGikempM3Bhj67HVdof51MJjrqKHOHob9vHYLIjEhaC1li0O/uRzpJDfnrGhwhOStCDCH5xdPvncpmYEZ2gQkoFg06GIxIkONNhY0NL8IzcMfzk5w2BqK9p5OvrGu+qdbcXWDowT0YNkuPEyqZulrShRqMrdHAlY6f9iVV6RokUFBXXdDy391Hfe+g2xIueQEnLlIWaumRjnN06PJh3oMvBXuuWKpYRpxjUAOygv7NAt8ahCdmz4oVmTHjU5sMto7byAz6bNnhP3zS+g79XQpGH5tdeyHAjbtpJ3n3bUmCVbtra1Hx9JEJ3REGbr1V2OZm1SRnvDOiUPrdJEAOdt3WgwZLAU1rWZaPhzZHwFRhR2r67f/mxLixgQ4EWinWMn0OsZChpPXWiEBlQwVHvVFxWau1GRTh4eyIfnR40Ionj2yb8Mx1WMGfoE/rzU7jfXHI1BUTYRQ3lpyNRqwtFlsorVlXLAd2v9PbXGu7vLkmIEOu346tilN8RBWECqZbHxdwEaAlEwoxg+jwlOLVoKlGNP0J2PFt1nG0YcLH5VYxbEoIZMK+61EMFZ4Znv5U7PDqBtJBJDFghyyp2CbpMIWNtZHGC1+haGZ63nVGpeQPBcsSO2ldx2ZqXTZZdZkcGpN4ECCFkNowU7HDcnFRfoWs4hL60KYKI/IKaxGKsT0TWNZnBhUOtg1O1CjGf2NZ5Rzu3T+C6G5y32gBA5I+4CRfBTsl9zvjO0AGXVj7XWNmH8SsSMuMhUGtVqc/B54rriV34hejaOjVkrU26o0Zjevim8Ui3DysZu7549WvZ6EBkPHi3eX5T6/DKAIoSXOVwHR5UOtZRzR+e301X9wmH+afrm7fRb6JnpaWGPXbxs6TC95ffli8uz69fPNmvlicvp//G7pEr1JHL0ncXn8Epy4/3oCcki+yLq6vPl3efvk8B1aD8OPl6wsbcghlFGfMC39P25uygFGiTm+3FcNBTStIeKrLe4yIJ2j5950Ctvgkwfv5YvmOyKsGm+EtwIct3pvZEQxo70QzrHtXxVgxBto0fB84WofDg+HmFP4BVIlxmdljjA4UWk9n9ncYhPpifw6SYosWcWoSSVgOfXfXwqf/A05dlhn2PpiKVEHXcb/AB4iuQSkGMMJVCxTAEwR7PcgxBHcvvf7+Evj9G9mhH3fTnyaT+2EojDBgSXONwDDY3xvzXhEHV/NbdHqMb4arMufl2OoKekhjxfPc9exXryx4/JOmtM4Gis52j1PypAfg4wgWvLCEmCu2kSblsC31IMawd9FQldOUJewJ4mpQUVv01ukeOpI8Y3aZlvDWOyX6rVhDi5S5p0ZJVeIbR5FvoeOUpcUMgCSwYNNGkUY1xr7DKv77n1E4EDh7s/ANwu9VxmnsiXrR62Q9i8DW28Hri1MTv07E+M8FTgZfCYYCmzX89NVjZIAEA88EKT5f7YMekw6YYTKxiy8Ombxw6dN0nvv+QMISJaCTAMbQKYUbmyjbrBWFMCRUJakopTxwxuUpMIkKm43+qVMs89wGqsTBXUs0cCvqvkMAzGqoLqv2V7iwBy7XS5q6aE16RLiva1o84Hkz9uwz7H/k6tz8c9G/Ozos0oBzc4fMgLG/nZthz9m9GuZGeGX6x+7y2/mH+e0czK6O+jTeOf177CUn6EpRig0CSebelDbmG0nP+JbJZKlxrftWFW7Mm5ClJFmZ5wDiAovxcJvKxyMCQOkLFOURA4YwwD6s3hD67NCnnEfQrfTtp0XmjHYaWBvr9gRn5NRyOl3WeleVi9CgrsZbVGU5u35Zwa5fVv+wXyZS1i/g7PjVV9U59qBfrSrLacr0AFC2XQ97lw223dMztnOZA1PZQxIe5UBOcZWzvgncirBpBl3UiPd7gL3ZHZDAAwI9GRDzSQOyEKkjoh5lQFLUOSjHb8NUbH1Rn3RUVgJyLrIj0i1xQD7NSwBw2Njx+4D9DNPudVnLNK2FAMDOEvce18oMEb1cuA9sLgvm2TsKk3WuWhb73M/PUk9Mu+rRoDgtDVZ+boyYWfgUI2QWh7YA1n/Ts+9IV0NysqrATTKJJzrC8Hvo10t6HPlFPU9lXsP4Pb9YD2hpiT+g47j8S7I5/w2QCFfbpnMeqjnkOapOfwjm8CYAaVYDqjz6i45p/AJvzE8wMKH46xT/B1NbRsPk6ECBwGkv+3J2V5u9uzdCezsZsUfZvMELkOYPzYw0E9KHpXpe6pXprkYzdFYjctyo5kaELRBeG1rnC5miQnWnnGgumFAuaO07VpEN8MIucp51OaVi1aBaVgWR99FNc+IgMa78MjPHxgUo7r3tWngCIt0hAj6enfwOUEsDBBQAAAAIAOmeLl17e9EDFwIAAAwFAAAOAAAAcG1lZGdlL3Jpc2sucHmFU8uSmzAQvPMVU+QCXvw6xrV25Z5jclcNIHlVBomVRBz76zMSIMB7iA+uYh49Pd0jYXQLjIne9YYzBrLttHGASmmHTmplE+FLanRYNWgtt1NNDCVjoEX3kSTJj5jIqPXJ1fm36XmehBD8kk+prqcE6Fdp5QxWzp5AKjeGrGO1bho0FBWNxiF+403zYMJXEylm8L7Mlqhuhprmgt7yellhOFqtTmCdIYo1F2DlkzMaX/MsVGwKECgN64wux84COiMrHr8E56zjhk28p8SKQ6xeU47hFv8OY1n3guAza0moWqqXGJzhWCQ5bC8rLaUYyML7GQ6gzfh1oWr/NbEL6aFjkIV8VyNQdijgsFv9pVL9wUbWA9p+QknzJEB8g59+SZiWBC3mSdbhjdcgaDpCKRWaR3Qc7tJ9BLOho2Org/TwuRu8wjsL4tGqpEkWmGTRHNgObHLYh/SRb78XcNwdYiIPMA7NlbtggAeSKltJXyzmbOY5a9dGqLKvCYpQ4nKbJXyo6ZV0LGx0HrV/+3Iw66tfsIqxwluc+Ze0o8vQJhtH72f8PM+92/O8CxnOG8vhMF3CPOL95YL+43yUZPLfyqvCBj57ugIh6fGXvQMj7W3SRFKIN/ru58i2b2eL/QNL8/iqaduZ1mamn3xlE0kuZPGlK34Bcx8dKWJTOplHvIfzrLDr6BLLh7d5Gy4AyJaKK4dXng6tefIPUEsDBBQAAAAIALqeLl1HK8UWLwIAAJYFAAAPAAAAcG1lZGdlL3V0aWxzLnB5xVTBjtMwEL3nK0Y5IIemIcsBiYrCBbgg7QlxgC6Rm062Ro4nsp1su+LjGSdNmmq3cCSHZDJ+fvM8M57KUg1FUbW+tVgUoOqGrAdpDHnpFRkXVQGykx69qnEEjP8phPcjGYxOK7X0+9G2k7c1qqQd8jYZRdEOK2h9WRh6KJQjkcDyPThvVxHwY5HFmClExigxRsl4W5LxnoosRxLJic1TUWliR5cC/8tW+/Ut45OBUlXQgTIggi+FOD755+GGXb3b2+OT9RN90vvxUGLjQXw9NvjJWrIpfJO6Hezr3IPWUsu6Ed1qoExB02Tu1cnsM9JbFzmp5UFoSqFWRuxVCl0yJqBEpQtPM1rnsbnCxukIq/BuDflMrFQOZ+cQcQ+qW+dhi9CQU151GCdzRZZasxOh6FmQIEQHS7jB5c3rBF71URJ42X9TeDOKZSlk/7PaXkOQu/inXBN6TatHLDwevHCr0KuXPetgPe/xbNoi4tvPXz7GfEAgGxpvgsdx9ou4juUeuJmBP9ygLpyW794FWUn1Vhll7hmbnAlcpukBrUgyi42WJcd6wYFivr07mAWymLl2K2y82RraZB8224BihX4fdD0LdH4COh4EeBXIOju0nOvjgL4KLElrvMe/on78lMvHfPn2btGf44wayzfRuRki4yqoZhoFjbQO+7EyFAp+Qz8JQr2mMTb4pv4KGXdPrm2AXJ0H03gK8/E8kNy5GN+DxEWer/I8Ti7mxrlrn4/5B1BLAwQUAAAACACWoC5dIoVNgDAEAACECgAAEgAAAHBtZWRnZS9zZXR0aW5ncy5wea1WXW+bSBR9969APNnalDgfVatIWZXE4wQZg4VJ1KhajcbmYiMDw86M3aSr/e97BzAmNUmlav1gYJhzzv0eYsEzI2KKLVMmJUgjyQou1GGpVy9w2etFEBs07ucsgytDKnFi4ArbpurKiFPO1MD48Gd1d9Uz8CfYd+MakdYKFOS7Ejio3oDainwPN5K43JvkRt/jOZwYpjkwIJVQsfXx5aCWTzrlk7wSx+v/Jo1cbeFFp/CC87RU1je/ku7SqjDHhrVtRZCFsknRH1gp/w6iP9Ak/5hnJnIosQV9fQGpLzw3/61Nlp0m4xPap91Es/HhNwMmDe1DFaqWgb3el6Z2+rHgPyC/DtHCQa9cMuagVJKvZKXKo0hSViR0Ay97y9Bs0x+N5tSeOXRCnszBYauAVcJzebQ1IHeO7821/1vZBkhdvLhfbYsUvpWBsCzrL8SWK/0m+s97B4yYC+NZx7dhn8/8ICzJ7YfQNweWLNJE9c0TjCGGo4GWZC3xGNRyTVmqQORMgazKRdu9qJnHJLy9p7YbksCzQ6I1qmA1HFmSU0RtZFnkGpvU2Knj0Rvfn2jQRRvBnmmUyAKExFjVramB8R5of6UjZz4jwRyDhuihdTZsx2yTFJRv0afVWlGepy/Hhs8nzoz6D2Hg3N2H1PfcJ+QZMywGrADNtMH7dUIXTALdirSdsYntzu8demPPCX0IXB3XtVKFvDo9hecyVukHrAmrorCWPDtVgkWgF09353V6a4G6eGgSdUjUFUSd0WtQIZIdJqQEFkytO6CzwHnEjJTwmR3em7VfGY+gvX02pWR0R+jUHxHtScEw8GbTpyUmTXZAmcio4hvIu9Cu80ioHUxp6E+I12hh7nNQFKIVvM5jo4o14JGwfCgTObwcNGamFITggi62cQziDQI026UkCPyA3jyMxySoaM4qGpkmRcFW8C7H3HVmMxtv2gTD84+DxomM6UZY8jxOIsiX7zgztXVD3Pre2BkR77Zy6vP5gWrH020G9Pxy/TbJo+8+TAnuua9sqdHYFxkTG1CveqkBYldM7WBCyk6/GA6HdRYU24DAXgYqsGa6VUN7QgLsZkIDLJpK9dNe9tfwaQf87NPH2oAFyzeCpymN8I8J2c1xY3uTwHddOsI/O9BOnA0b5zeQpi80FmypjobCnmFCXPeJjgP7NtzPhSaJGLuyB2mxVG/58JWGgT0idHYbVi6cH8ARS1D/XfDIdlC+AX8+gGEHuXofTB6JFx7AlwcwVp3Sbr+ZdCy2UPtcR+xQau8jsc5eIetkRYujibKHjG6qSYJTQh+Rp0WmG9uSf+NhAhf7pv9SCI4jRL1UdHiO59g3sG/BvoQ0/vlDq3VM69fWcfcbf1RvfmrotyTLiQU5W6QQHRQP3zhdgsY1flVooGmwPKrWX0++coeecuW408XieHdm7z9QSwMEFAAAAAgA1p4uXfaDzLdsBwAAXRwAABQAAABwbWVkZ2UvZmFpcl92YWx1ZS5wec1ZS3PbNhC+61dg2EPIhGZld9w0mijTQ9tj2kOmh2o0HIgEbYwpggNAUhTX/727AEiCD8l2kzbRNCmJxz6+/XaxRAoptiRNi53eSZamhG9rITWhVSU01VxUalbgkpxqmpVUKaaaNe2QXVFTfVvyTTP7B7zO3HNNq5wqAv/V+cyuTnK25zetKHxJxU5nYstUTKTY7JROM1DPKrVzGpIt1dktk80uzeg2VXzLSyq5Ps5ms59bm0LY8YlVyw9yx6KZGSK/US7/pOWOLWYEfgW8pkem0hrULUhRCqrNhBI7mbEFUVqadzCj4DmrcKxbVaUbIe7UgvDKDuRc1UwqwMxfJunBLPS1kL/Je1ExM78VOStPTeZMU15aS2aznBUEVuSp3SP2TJb0GCLwZglZkgAB+L43n2RqH0Tk4h0YmOkVrIvN09qiUMMujJURE5khXhCIPqkT9pErrcKICAlvCggRRvA/wPwTI8slmVsRxk0GBKrI/YO1uwCpdZ5IBtaC/rBuJedFwra1Pp7aCiSAve6lAMUp0AEwxo1cMynFASzqdmue3TF0HRwLZXLDdBjYsSAmQRChwZLXYdTuQKyteVqk1W7LJM+anV00gigmqE2qZZAJBoQIOhEOIacb0ckTrioa4k4DF06Hc/LWxtSNvyWXnuWOWppXO9YOgvsrK3aNMPQW+9YtfMFxf9mB8Ztb3S5xrrnRmMyTH66NjeZhsNdyP1j4cLqx2BkAmA42gbNM9ffYIRuBbrWNqgs4uNojNa12tEz3mKBqitR23mStXfQtMfvr0rZXyh5jrlMNNdmC0aMujk7Qts/acyQ1hOjbc4arT+GcifuIdGZnV5mHbPdmkPEN4Yd8fx59z1A4vZFiVwOJ5R3T7UkWYuXfUgBqYZgZkzt2NIocFZApW6RJu9DVISMHLVitPdZA9LZ2AcgB6gNTUWDPwCNnZd4sawyxghpba4hdWtEtCzWVsM5YFJMMgs8h1xgcaiXkB2bUOia05FThWJdlOGESDxPUnleLJsGsyKQUByYhwcC3+yCHUxDR1JwFD5256H2GKzzNPV9AXPYkSc3PxSWz53gmJMsxx6BbYHkYhoOOwbkPnrdeQu5k0ZRhMCEZnKiKuZ7CU2cVrebr1eUabXaKMZe6qfmavIPCkryeE1YqZg95F5C2QUhvr2470gDQrGQZtmGdgR1xmuCioY/xDyAD0YHHJYy/AnAwoqFwtIcxx3qrAUU3wtZdKcLcR2A9JnmWGsmdwcNT0+5+5BzECgIK+o1h2DxEw+qMy40LVngvOtNAa+hvSx+fWkAjFxOHIKSVWkJJxlWmENASqngFTEjdWORFAlYjUt7ePsmfFynYP2gTaovGSoyjgqjSDfS7pvq5OBpfwOqLN2/eQCgvrHOmkrOLH9fDHCtZFaKKCBl6Nc6q6WCYHeO12PwAAQBQNNMEBg7Abe/0G+jH9W2aQ00LsHUNppd7ga0fj7Kq8aA+kVEu4o6nw9DbrYPYN4P/Z/C7GvQfUgAT81QVNkYMExR/35H3DOrARuhbouC0VURA8YNgEouTMZVi5wIfRpY+Cfm9riFUlSZckZ2Co708EgFjClhijUx6SmyNBtfbr6IeuOeOD+fbsOp7FJiufV3takr2NBdb08Tk9AYwuJsC2p26TwO5yf31tO8NCcbShAETtp7hxRRcSCX8IxxjLsyIfWn4M40HmprQumZVHoroy1WZ1eJqPTZ1WPoxlq756AIaRU85C5AgowPhyZWi2z48KbyJr1cxnk2c55STd2foMEln/EGlyNynjxOPI/C5g2gHplV344igHQiCSbIaUdhonUlyXOOl9NszGX3S5A7S8wT3GsZn0bzbN032b+BYNR91eBGXQh9cQb7VDGA1z83XzYlPBUgY7l+XYQJ9TEf3Zc4TTEmPuOg5bkW3jTLLjDYBRh9JWh5HXxFoa+JySB9r841vGmLbpONsS5gxQHiTMNGjx4ONEy0v/lh5ygBTGjwTbMGGcxEPie6j6owpXdGKPRFP1W9P6SkMPscmr+caI9TJfD5abTX90hYP6v+/slqdUDPqWsC9+hHrMAGaMuOuTryjmn3MWK1JaK6yf8WLnph8AJDc419Mil/4nmNimaFzV44uy4f37eaqRnlpu2yfhrm77L82dwwbKIa5d1kX2vui5qahURQTd0u9GN7hmeuF9sZ+eMnQCkAk+yiKPTZk7u4bS4XV3PsMFfs+Jg6GVl04bqb2K//6dR2bEXdLtTZXTNfwNz7AX2jR+KJpLMO+XogKeuACOuENze6C/sbRp66tyI966lpN39PzXraQJiZqaGKMJUJIrUyqVCKFIyvwopd0t2zxCVkta7qhjizxtNIxekVwPxL5YE4F5cEVWVgOgAqQMjSRAN7Cw0/w5NqZ/dRteNMlurM5vIQG5xCRl1P2kVeg4eUomDPnN94Po9LLxJDh9XVPSgcYiJknVziL3UivSZ0IEGoHF8bheHUPhrywPHyxfrDRKTpMPisSXk4NmVsEJsOJxdH+K9by/rBIrooHF5Jo9g9QSwMEFAAAAAgARp8uXRclSGtWCAAARyEAABEAAABwbWVkZ2Uvc3RvcmFnZS5wedVZbW/bNhD+7l9BaB8iDaqXrt8yuIGbKJsxxy5sp2sRGIIiUY0WWfJEuU1Q9L/vji8iKSlu3LXD5gGrRB6P9/LwnqOSVuWGhGG6q3cVDUOSbbZlVZOoKMo6qrOyYAM59CcrC/XM/sqzmr4YpLh6G9W3eXajlr6GVzEx3NVZztT4ro7DovwYZqwcDAbLs9+CyzEZEcdxBmeLYLwKyGr8ahqQyQWZzVckeDtZrpaEZe+LCJS4A0KyhExmq+DXYEFeLyaX48U78nvwjoyvVvPJDHRcBrOVD3JxRaOaJmFUk1XwdsXVza6mU5yrs/iOVt1x+oEWdWjMCuE6p83bJqruKMg8bPUYozmNMUzNyLbMipqAQ1xtGmVV+EBZuK3KG3uUlbsq1pr4WFwWaZbQAsaV7Caq49u+CQbvzWp6T+OdZQe4Uz3Atpmx5DaqknAT3TcDBfhDk/daAvapqyiumYo0V8VqMIBprxhAY8dEEM+Di/HVdEWOlpNfZ+PpkbAGlIQIGC4z8H5RKZ7MzoO3rRRnyX0o06wSMJ+pxLti5OkaZPJNFRoPoGYf2LbRllZhWSW0OgxxYqfQEDex9TV4bLJro9RKszWVZ5usNvJtW9BOqzWbUhqi40qqu95MuBrXmZ+/DmY872mW5y3I4ZB2HUcqysr8A4zBwy7vGTdkt0XeaLqYLwKAGCbAbeLtwfRFsAhmZ0FTK1wYfgLkRLKlZ4AWM/muGP4CXPLsA/0maPlakNyBt7eZMAHVqbDFeYbVrDNulYybsrwLrRG2uwEMoRF2DjV61AjkagvUQO0zjqV8MIjziDGyrMuKnoAoIQlNgWGyAuAZulAwU59TxgmgqvKECP62QAbIHS5Oenp4uI0q8Ga4uUuyyhUvbLSqdtSH85BBbSrv+Kteg5sMUQ2ohE3crZ76mMEonwevCtcjESOxNgJ/8VAcM8riKtvWrqAqb9A4w1eiDsP6GLcSrMg1Ayu4jRl6+3hYAQWmEMyyejCWLMqPjUxFgYsLEusNoySR9U3GD5SckCSLa488e0mAcLQhaUbzhIHqa8spR+PL8R0BKHgwaY+PA+HBvwbVwVtDcvDM6c3xbd0Wy4GQwW/qTRMYV29zGu4BT2iPKnDtLQw6AzFFZPCoKAweG5yiIslZqJqfZa5c0ZLTKF83T0lURxA2jKoL8fWsiSGjNWQigoLlWqE02xrX27NIWUEcQZSOLXttWrcGO/BhmOw2W+ZyXe8pKDFkPFJW5NNnn8gdRniaGpUfsGcCDDRLU4+ksCAFsEiIrA86E7sK1DUnw7UmOeycyWwZLFZY0ua6a/t05B8N/wTMuGJT77NH3oynV1Ctjbnro9Oj9Y85baRADCKFTlgbedabPCeARxfMG0LZqSFvWPytg2PUdXl6mvp7gov3nqZvERooi2ZsTJ7pyGpm8zXKfHE+fTwjfnNCfIPx/Qb6fpvMfUllna1UGk791n8eGOx3LeuM4E+bax8EHtRrVWfW8pUfcvWij/q6uxvPL5cyzr1aqc+5Gmk7/ZhK52IynQbnDsnSjhFkBJeR1fj3wCE0Z5Q42Nb0BMLz92CyBLYd9UHSFNq7+Ulnwz3Q4j5dvT7HDsVqXpfByujGRqe+bsRGp+SP36BlgnZldNrjH/7cvti3Egyuej3R6TuiIKrPZLmlRWg3WzaVPuHMSb3XoliL0lZhadOxcpbBNDhbkR/JxWJ+aUdH+C8Oxki0r4631iYi/UkTRdhk4VANlawbPDInJM1LuFkcVjP2VIvH0qnMFRA+8p+UXzKenbcc7TnaooZ0Eiy97QC+idOuaJp2uf1/I6MyRNz59nVjsuTts5VvKWORRCvXYjXvWX28lvzraV8Ey/n0DU98yyXMvnF5wlcw8EvH3BWLO1mHpU9JPTKrvv98iVj95sYgifabhc3mVuNGdhi1tm5SfusGJai3uTH5rZuSQb/WzegA0v1etCvNEe2jcsfpKdz4s6VbMXC8PhZvYrKXxVvx6mdyo+NVhvQRzOOQhJA8gOuy/GAvh1g7sAyJc8R7529RippjiwWnm0J22EZykakLP5HAPXK3cXlBciuRPBgWV4RjQ6/yzrwOF0wuf96Vw07lUa0v4X/tO+sn+9KmzD0h2NvLt1YCnaZ0ifhJ4cbUljQaDCL4T2smLxmjneXkWZ8s+nHCS11He1hBaZA7kJ9ayrJUx4Y3ibOyoFrHZw1GCJd9Zbdr44HkaF0w+jFhVXuz2HneMKVw3wZL3d7uTMEPndNemewYYx1QX9eEO/zywV0B234+Pv7ebK+8nC/OgwV59Q4/tp0HyzMynVxOVoT7zG0Cf9cd2zvt5r/vgFUjDvTiNmIhVEkkt+5tVhR72ZlgJRYf1rD8QXHO/wHQumwrXXr+aNkTxqi+E4yxWlAgaeLKz8WqSfOk78/7yE+yM9fULvxfQDVkg5GirFtgxlBagN4bRZ9ssgK/BCqgvDj+PoF1WqGVcO9j1EcD3Uu/fD6BklZnG2r+JYS8HOnxI+ggoLE87TYs8FP56W1PzAzBJcR59gkvvzJq3mcVv3bH8ZXZqzJ2xz/rhXWZRA+a6DlL6ZD/QM7KHSSsLPKHpnTUt1FNbmgcbSiJBHiRyrBnFA3vL2RDK4or4qgosuI9YbflLk+4ITfcLNgfnhKo7sPvkv6z+XgK9SBwl1eXLhuqr5ieD7gbL8l9X36s+rgHMpht0GlgYMSHePJ7My/Q48o/d7h7Tv9W7rEdNsQzYsP2hw/7N1+Qfs3m31VyqThvK8Z2+SBIyUbm2rl31ryFGR57fcjin8RlVTA/j+uy2gIb8CYCxJLtMwA2/N9hBtQZbu2rMf9dlPRULdMp/8DS1IujvwFQSwMEFAAAAAgAlqAuXSnPaazCAwAA2woAAA4AAABwbWVkZ2Uvb2Rkcy5wea1WTY/bNhC9+1cQ7EVCZWnRNhcXe9gmDlAEbYJsW6BYLARGGtmM9VUOtfBC8X/PkKJkSVa8LVBd1uS8GQ5n3jxupqqCxXHW6EZBHDNZ1JXSTJRlpYWWVYkrt6XgnwZQ42qV5AKRvU9TvPvw61apSnkfm1LLAuzC36wYfTWBZtjXuYRSd+Zf7u637JbxvdY1bqJI1DLUe1hXBF2bRVIV0dNPfGXRKWSUpSyljmMPIc8CRpj4AM8bhloFlN3OJGtXJmyDPGAmparRGyZLTZs/3LjUzCczRlccogz75lNCIkwvyN+/eXMf0zJ+t/2bSbTlkApSllWKiUZXBZUrYWhqhZ+q6sAyIRV7EjlVjftDfJN86E6lnNyvqdldhszu19TsrkVm92tqRkAkJ+vdtSy877Y8fxEZ7kGkoDBs6lRo8Fr+J4Ja3+2oWXzD+Iff1tt0B+utwOfox/CGn2ZxYngiaJyIZA90bHtaMuO37V3RBvvvVQmjtu9Au5bXQu9dv2uhREHtTmWi2Rfr4jxHPa5pywC8Ds2oU+0o9/qBU/nfwTN/JOC4MQNEqzk1emRfuz670BL6e5tjn95tPXDwdtw5fxoytHyLiUgx0tA16M0AQMNZEu4zmh4GrJ2Yzcf7VscKCkFzUu6oc2rorMmSH9c9an1G+cGVYA1CejWOBVwNQfOvr4awgFmIM0HgmECtmTdQ+WP3Y2v3qQEB+8uMWKc8TKDxmLUMiF8lGn7Q2UJr5REmMDl2BlIKS5yJ16cqfR559Fhy03DU5MK5b0RkiE6aYATFMhFy2uD8JVHJuFkyWvejakQjh3TDWkrx9DNrTRoPm1c3N48nOi8zak2W83SIRMsncBNkiTjVuIUBGyU6L5Tl2aXLgCJ5EAFrUOygnwM7njzq4GOdsxuEekArkUgibP1NVqSPueeo0N2ACvqHasD3H69rg9etXRLn476d+1CqToWclFiI1ZJZvexbJ8sF5bpSrDHswYZ4fLFmk2hZX8GotX9PUReST+ei5Uag31aKHhujzBIrfjpDFmV5mpYp4SijiwKObLPKxeZlvqhe4IwydctCqANonJf2O3ZvCttZGUksMqyYeUPphaQAw1vFcC8UMJtzOLgXB/OoBzz8XMnSQwoFqXcM6QxZe74l2NF0zZ0eYp1L4lbQzegA9M/3tQe4R7gj1fkqdIuDP2bFGTxlxr8jhmvAEOR/4kbU9vmeItObOVeWRNn+N0HMGf+bsaDerowELA4LZnPamYQpJLIQOV8AXrJ1pvMvcPeicv+Fvl8BUEsDBBQAAAAIAOmeLl0/UQEObQUAAGITAAARAAAAcG1lZGdlL3ByaWNpbmcucHnVWE1v4zYQvftXDLwXaat442OCKOiiTS9F022zKNCTQEsjh7Ak2iSVxNvtf++Q1AclS0666KUGklgkZzjz5vFxlFyKEpIkr3UtMUmAl3shNbCqEpppLiq1yM2SjGmWFkwpVO2abmjRDJRMP7rVq1rzoltIi8p9BCnyItEigrwQQtK3xWLxfeckIMMvWMWfZY3hwg7Bb7XQ+COmXFEg1wugj+IZXoPS0j7hC6a1ibIfyhmXyV6KzbXZh2k7uEGlkw3PmjH4Cveiwn6Kqd3ElMRUlCVWGWZJwUuuJ9Y8MpklJXvxd8OXPaaajHLEZI8ySUWlJUu1v0iy5wSzLU74rFDPTam9RDaVxqFmBc85qmvYCFE08TPVQrNYZJjDOKBgL3na7hNRTLp9COHi1n1zuL+DuyarD6XIsDCugFxB62pFC9JHVm0RpKirjFdbSFkFLMtAlawoION5jhKrFFXU+ERFPjlNHiEXEnLjiKrJyD0vChWBEqAf0WRAoW2PwAoa2SHuFTBQuGdmHDa18bxqkiYmV8TFl+BydelygvdgE6W/wXp1CRfuMQwbWJK2isGYPRGUvEpGBYmgEpWB0u3bjZqhsxA+iOIJDWIK5ROdriekxDdH2EqeERFTViu0wGa4J9opEJWLdNVRFWKgtNaO6gQZB15RjgR7sI5gfUmf0G1nPntazuGDG+9GKR0a71I1cNDPICd6PuVKn2HY+eK5dXcbD4Hq5r24990gFmq8hLi68+tnNSMwlpFN2Py+umoLZkFJjBYE1uh9NBSHaFIKojNaEJ0RAzc3RQR3XKfI4EzYjgAc0cJN6TNTPN1Rbl+68+9KHi0soSY0kWpgMqdAtGHDX8s/7x6WESzvf13+3cMsGSd2/cGKGu+kFDJYWqOypuJsEMgGiE9kE7ZOjb/gEm48qtzAOjzn0qwEs5JteMH1sXO/Qf2MWMElXS0ZrGmTgXwmFirKdOIoDs/g6PBFI5DDoVv9zW71yK0nv+Sv012CqSUUcGUhM3M2y5ZN/kQPXuerk6pu/UXns9n3HXySaI4lBf7ErbYW/FDTN4LYbGc4A2wjjLzUkkSWQKeInh95QXqMJeOVMdpgIZ6Btlh5/HTS6OXkhg0qb8i0T4jUPuOZkdu4veuDzuK7ntZR/3WgI+fhGu9AxQu6576nCDwQJ3cMJ+Od8zbk57QfCr13dQteymu8uLJMCPzcLD/oqPVGN37pjVE4n7cTRi9WXx19o2FxO4OJNU2l/Quh3/ENF4MXzPgwelo3x6fZiuu54DywXo2tXTtzoCkIH6fxCfbwOTM1e/k1d9lAsoNBAYwEk1L/8vHnO1JsT5taFvWqEPmRRiPljAZeT1AYWI5qNDL1cJ6wclLppCsC06sPzZe/U7ROnqhfJrlC0+rQOwMBxMu6tM2CBY3lmipme0ls0LStjxVOW061WvbO+3pNkcZa6fli6dliDYX6Jh5fHe4Qf0NJP7+ppP234b6v1HOW1fPFHJu8WskfmnvE4PLmOuo31vE8jA2Enz4+PLwOoe3QrICPxXoIqYeNvW0tOUZ8smOmO52GbkKaegh/oncTD8PlvWheT9mG7mCnLz2O5q2GypnzLb13ZyewtpC1DW+TeMXkMSEK9H2vC/SIavjWYgZOe1w7PNPeVmLKgEZP1//LVnimDf5PW2CT2MG8Y4xeDFo2xU1P3JU1HuDWMytukOsZFjeg9aX104/PNJHxsKXs7QeJx8PHMS6xHk+32MR9f9NwpanjeSCow/dxWJtbcwYLxwkPCkeH/wES9o85OCoVEoNDeKLgh1V3BZAIHFbdP1Cscvmz3l1ipeHi6upqoGKmgQ8sA+2BOYQR7PAY263DxT9QSwMEFAAAAAgAlqAuXSkLwuJCCwAAqSoAABAAAABwbWVkZ2UvZW5naW5lLnB5nRpdb+O48T2/gtW9WKnXyF57D3XPC2yLHHBY7N2iu1egMAJCluiYF1lySDkb3zb/vTNDUiJF+iPVQyKS88XhfHHktWq3jPP1vtsrwTmT212rOlY0TdsVnWwbfXW1RpjZupCKPxX1Xjign2Dm3zgxZataNBUfQKaM3hGTiyfRdFNWt0XFt0WzL2oDo91cW4mat09C1cXBMnsoar2RjtEHGn3ar2pZ/rOWRK5p1bao5R8CaKoHATOdLB+4xpl1q/hOyVJYatuiKzdCOXLEFXALjTLQohGyB0eCfFco3e+VBpaVBWurSrvlX+H9/aefnXB2eKtUqyw0yiObe4ewErrjK9kU6sA7VVROVCX1g4OhvfiLumtVcd/L9BmG4urq4/t/fbj9wj/c/uczW7BvVwyebPP9JpuzJf2/m5o5vVOiqGjavOpsyrKi7oRqik5wN+ngOzCBmsDpbQRt53pgUWy5h9EPx2jeAuC+XF1VYs2sZrkSj3vUzIM46Imd6w47MWe6U2BUAs+qJ6bnbNW2dc7evMP1OUmCqKAHTyuze9H5xKZseZcTrFyDHXURWTD/ioFFT5BWzt6xt4a0Rx7/Ledv72heCfCehmXTbPZ7Ky3a1VVZF1qzTx9vq3tx29zLRhgytGEuG9lxPtGiXk+ZFl0H1qHzgREuzJCVWwtXrIcsEs4xcRgWiK/A0vle1fmIONoPUCA7GpCqFZh+txkBW38BcN99JiMo68UOKnDtMayJBT2oHxkisuhqi9DLBoFxlRc7iVYzqNJMK3GPQSzHo04iMFGDl//SNiJkCWfX8b1Gd1uAvYSLldQl7urANURJFO0bGHJ/tgAjyk5U4FHgp5rO2DtZa3X9zoYVz5g8luJ5B2crOxTkmUFwY89MNvYMzWYMI6T8PNvvdkJNcvanBcve//bl1+zO5+yIpZl+y0Al4MDP4LOd7Gph3+9Vu9/Be0bOXHbyCVe+KIz02abQvN13St5vOg3TP4HViZdBUMfy7mrQIsk7ZU7BvS5mhrjTXH7sUGYF7LKpJt8y+LsDp+tQOoMFMl5fE9RL7m89UNiD3PVC87apD6FCrEJB45o2oknjvZbx/DD0TDQFl5EK8nxQudWsQfVshIxePFPIqU36sYaC0ayWultWsuzuLjWbY3bpdDJnN3BUhg8OXo4Y3WB1BDoyfqcV4hbZ+aBtUhklqV5tobjoeQuzYjSIdpcHIHa/sBCi4lO2DbjyXgQLnUpAqvZr0s6sxoF6HuF8x75sBNup9klWUDaUe6UAuD6wqi33W1JLt5GaOdNjhWZ/CNVaNmWru2mC6GrfoaILUMkGEq1QmukWKAn228+sLBpW7CvwcvAAjIvGzCMyF/mBPeap9QiYotAYewUd9HMpdl1Qs1yocDxlgSeMSo5xUOFowxMRq1gsM2M1GIOzO5NRT0CZYHQX2oyZzBmIcRrbhK8Rtpkk7CxLIodejejG5wcaY7+PydBJuFMCRZxNJIPDYgViBrnvujhtBvnLOMyYeT8VET6I/WBiDP8quw1HD3CVx06UU4N3IkWhknCC4JKpI8igptjC7SQLO+Q5CwoyPzSPq7F8LJWlfl6MKCBURVccDQZGK/QaGqdVzzKTFQ4s+zwOvwm3jAwi9lPim8UBo/fdlEgJukRLVgMCiRsDWvkBzr7FMDZQhAsvUXiWWjZgt00pJkaz6O15HAhw0cUCfI/dBGejiJDY92nEIUgQqnHRYG2aInpGnCF4xFTNGnjnKHdZa0Q6Xhl3Js76Jjx48HCpxnut9VpzcnNSaeDDnK6zVEP013P2X6I599zTVt5eHe5dkuD2BldpoXzNwGkbuKTIPatJpEqDtcxoGweh4WLerowbmQXd7lUp/BnINWtIvA3NRgShjLmZwR/ckvvrbSGDCCEo9dldYnhVQC7LQ1peKLZxxVPgLBHo2qc+ZpgLzRmVWcrtU6IoOas2w3CZmRuU0xnODPq6mf3ww9TpI0nBaCemY4ZvsOz9O2ydDVnCtEMS0Shp34OpkrwYvUlxQ4V4Iv2MjdbofOADZqBFo/eYRoKwjkrtWYXK9XFGHahYx0aCgdQ0uOnGKtjKhuNW9MJPV/1sAqF45pDh4Tqm4RIaYgVLY8scqXncXYuMbjpsfBqYaD4EEg0Vpo0epRLYitkVwJ63CitR00kBreGd7ugVRBVf+ZDbvT7EDKF5CznPrU/sbnGjUcYcaCzHfbzJNqe6ckt15cDPvw4Hl6be1pI3Kk+XUKOa20xwSzHkkVmyrKC4Z5T91NZQ/PPv/7qBZPCjK1nw+IeVCwtntDyQxW8q2jONwse4VmILuNbv4aR36FoC0v1xlmNaI+UkKvbBG0E8rzOaDlDWhUOq57wIH7MfE6/b0tyvSsEhXYpObm1J7wOVdQuaMmvnYhM2dM5tDTU5yW4/Qp6YQHEdl2n4mJIK6iQTRv28NDc5KANH0m2DpRxEUSqUIbXHl4mXfJJHpxHrNAqGUGFRZgrCn3uwSeljDOKF1mmOcVhNEhpHFGLrmkwmk74ez/X0faREeZi2Vwx3vW+PiiDX8o8Ln1QHAZEv5Yr7QPuIvyX0TN2WsZhZYY09GKuZLPSDmYQEfRNK9Ih3yHHrPzaEoFha4GgWTMUuYIVZRMJNmRVpEYkYU2nagIgZIg1480mYYYoCGlwDoUpU92LhGaGbS/Fs1kLw1X69FsqhBJOpxArGxhFCQcjo+QSzMVaXxOrOYTlTWODbsUyNjzW3x9kjeItcS0HX5seZEmW73UIKFxWv5VaSW4cVuXvSRgnc8dPRwvsidMRmyF4eZ/37lJHtLhJCxDtFHWBFgEKoouwASzzvTIdvvBZjr4rmQbV17fTqxryCP5DoYowHUdcHqNCw5TuUR+FsuqgiFfAdiOhVGf1kGsdJrn2cfjJtyTGOP3nGFsypzXpw9uOC3bwiWzuWUKpTL7/GpjsF3eHrzQwbULZl7pVziQLxcaYh/J+KvifYL06xB8vC+KvlPRQBRzlPUaF7uJot/nJzcQ6oigOQ3xaysfafNi12zbwzrQoJBgR2wN74ouKXVY6tWd61VfA1Ch+TQ17Ny6Cd4GWqJ6cUA370qthbDCA6du/QeCdUbQTayNmf2Vvx5m+vONDT1EcauID+qKTFbj/YyUcqFrIx9zOXa59EBJpo5FCFeBqQvihQd/ez6aiGAsN9oP9c7j/OcudRbXO06RZhhOecwHPf1gYW1Lk60QSk/0e7eXQ1IKhRZzVBkD7aSKpaCbyfSAC7TiUB0iABFLZ25uySesUg2V6GRTGjY7BB9U3ww0xSL2HV69qiUbGcUhG2i+YucCVO/VmUe6tCzJF2lIKE2H8w9SPBXpCFs02h8HP4MyG4QQLOlVQEd7y+yvoEhOc4ykkpkSE8YIzwoYeIkTqdUWlglXJx4ZDZgrgiRDc4Bod1Zw8Hg6TL2N+5wAHSawLGu8zPkzf8lMJNn8eZ39G2T0b9rqG74xCO9XsIB1sdhOc7UTCZMvKhq2cxhpmjPtHfWX1/MJPHfK/Cr5Z1vw8apaIF9TN6+hQ0UoTDb8+mcOCy6pMu5dCiqlxNEQfp62sI30nPBCPjvxv+R271ZyIqwaRN8mITtHzGZni5CRkhXm9GhPf/mBIhvs6cDMrrTYrwXmFWBH+paeHzcvK7FRjOMuttznwUdqOoBk90Sr0SeFSbJyqa0Jz9Cr3nOUWJIhH7T8a02K9+x/4BRsd2UFlotpZKd6Bz3UnQuKgYJAFGSQB/VNCQVt+YX2zSjbQ7mB+2yce9rGA0/LiAGGpwh/BaCRXUoi62q6pgas4majnkHKjr1TJKzGbWGbkZ+UF11LtTYC5wrAv6HVG/ErW/Ubqr/wFQSwMEFAAAAAgAqp4uXaZMYDOnAAAAFQEAABUAAABkYXRhL3RlYW1fYWxpYXNlcy5jc3Ztj9EKwjAMRd/7FX1UiP7DKOypmw9dPyDUjgVqOtYO9O9tnTIFIdyEkwTuxUCYwCFHJodB9EqaDC/F7EV/Vudt3FlHeCPZatiGQ6uPHxbiQlf8XlyClx2lBFVqzTMJaxSYuObJLywVBhrjwoTCqsgMVbzL5NYs7GAaGPwd08kgy4Zz8RmF1Rp0XCkRcnnTUuOID5+LzR0381yzlIYB3VTYO8Av+XPwBFBLAwQUAAAACACqni5dPizlpS0AAAAtAAAAGwAAAGRhdGEvbWFudWFsX2ZhaXJfdmFsdWVzLmNzdivJTM5OLdJJS8wsiq9MLY4vKMpP0inOLy1KTtVJzs9Ly0xJzQMy8/JLUou5AFBLAwQUAAAACACqni5dBQnH8ygAAAAmAAAAFgAAAGRhdGEvbW9kZWxfb3ZlcmxheS5jc3YryUzOTi3Syc1PSc2JLyjKT9IpT81MzyjRKc4vLUpO1cnLL0kt5gIAUEsBAhQDFAAAAAgAlqAuXehoSZbxAwAArAwAABYAAAAAAAAAAAAAAKSBAAAAAHBtZWRnZS9tYXJrZXRfcGFyc2UucHlQSwECFAMUAAAACADWni5d203Hp6sEAADLCwAAEQAAAAAAAAAAAAAApIElBAAAcG1lZGdlL21hdGNoZXIucHlQSwECFAMUAAAACACjni5dAAAAAAIAAAAAAAAAEgAAAAAAAAAAAAAApIH/CAAAcG1lZGdlL19faW5pdF9fLnB5UEsBAhQDFAAAAAgA9p4uXeAyZvkaAgAADwUAAA8AAAAAAAAAAAAAAKSBMQkAAHBtZWRnZS9wYXBlci5weVBLAQIUAxQAAAAIAEefLl3FPOS4zQQAABENAAARAAAAAAAAAAAAAACkgXgLAABwbWVkZ2UvdHJhZGluZy5weVBLAQIUAxQAAAAIAMaeLl0+jqfXowIAAHwGAAAPAAAAAAAAAAAAAACkgXQQAABwbWVkZ2UvZGV2aWcucHlQSwECFAMUAAAACAC6ni5dE7ecJeQIAAAhHAAAEAAAAAAAAAAAAAAApIFEEwAAcG1lZGdlL2thbHNoaS5weVBLAQIUAxQAAAAIAOmeLl17e9EDFwIAAAwFAAAOAAAAAAAAAAAAAACkgVYcAABwbWVkZ2Uvcmlzay5weVBLAQIUAxQAAAAIALqeLl1HK8UWLwIAAJYFAAAPAAAAAAAAAAAAAACkgZkeAABwbWVkZ2UvdXRpbHMucHlQSwECFAMUAAAACACWoC5dIoVNgDAEAACECgAAEgAAAAAAAAAAAAAApIH1IAAAcG1lZGdlL3NldHRpbmdzLnB5UEsBAhQDFAAAAAgA1p4uXfaDzLdsBwAAXRwAABQAAAAAAAAAAAAAAKSBVSUAAHBtZWRnZS9mYWlyX3ZhbHVlLnB5UEsBAhQDFAAAAAgARp8uXRclSGtWCAAARyEAABEAAAAAAAAAAAAAAKSB8ywAAHBtZWRnZS9zdG9yYWdlLnB5UEsBAhQDFAAAAAgAlqAuXSnPaazCAwAA2woAAA4AAAAAAAAAAAAAAKSBeDUAAHBtZWRnZS9vZGRzLnB5UEsBAhQDFAAAAAgA6Z4uXT9RAQ5tBQAAYhMAABEAAAAAAAAAAAAAAKSBZjkAAHBtZWRnZS9wcmljaW5nLnB5UEsBAhQDFAAAAAgAlqAuXSkLwuJCCwAAqSoAABAAAAAAAAAAAAAAAKSBAj8AAHBtZWRnZS9lbmdpbmUucHlQSwECFAMUAAAACACqni5dpkxgM6cAAAAVAQAAFQAAAAAAAAAAAAAApIFySgAAZGF0YS90ZWFtX2FsaWFzZXMuY3N2UEsBAhQDFAAAAAgAqp4uXT4s5aUtAAAALQAAABsAAAAAAAAAAAAAAKSBTEsAAGRhdGEvbWFudWFsX2ZhaXJfdmFsdWVzLmNzdlBLAQIUAxQAAAAIAKqeLl0FCcfzKAAAACYAAAAWAAAAAAAAAAAAAACkgbJLAABkYXRhL21vZGVsX292ZXJsYXkuY3N2UEsFBgAAAAASABIAfQQAAA5MAAAAAA=="""

if not _SENTINEL.exists():
    _RUNTIME.mkdir(parents=True, exist_ok=True)
    raw = base64.b64decode(_PAYLOAD.encode("ascii"))
    with zipfile.ZipFile(io.BytesIO(raw), "r") as z:
        z.extractall(_RUNTIME)
    _SENTINEL.write_text("ok", encoding="utf-8")

sys.path.insert(0, str(_RUNTIME.resolve()))
if not Path("data").exists():
    try:
        os.symlink((_RUNTIME / "data").resolve(), Path("data"))
    except Exception:
        import shutil
        shutil.copytree(_RUNTIME / "data", Path("data"), dirs_exist_ok=True)

from pmedge.settings import Settings
from pmedge.kalshi import KalshiPublicClient, normalize_market, tick_size_for_price
from pmedge.market_parse import parse_market
from pmedge.matcher import load_aliases, match_event
from pmedge.odds import OddsAPIClient, OddsAPIError
from pmedge.fair_value import fair_from_event, blend_fair_value, load_model_overlay, load_manual_values

from pmedge.pricing import best_binary_trade
import pmedge.matcher as _matcher_mod
from rapidfuzz.fuzz import ratio as _rf_ratio, token_set_ratio as _rf_token_set_ratio


def _abbr_matches(short_token: str, long_tokens: list[str]) -> bool:
    """
    Match common sports team codes against a full sportsbook team name.

    Examples:
      BUF -> Buffalo Sabres
      CHI -> Chicago Blackhawks
      KC  -> Kansas City Chiefs
      LAL -> Los Angeles Lakers
      NYG -> New York Giants
    """
    short_token = short_token.lower().strip()
    long_tokens = [t.lower().strip() for t in long_tokens if t]
    if not short_token or not long_tokens:
        return False

    # Prefix abbreviation of a city/team word: BUF -> Buffalo, DEN -> Denver.
    if len(short_token) >= 3 and any(t.startswith(short_token) for t in long_tokens):
        return True

    # Full-team initials: LAL -> Los Angeles Lakers; NYG -> New York Giants.
    full_acronym = "".join(t[0] for t in long_tokens)
    if len(short_token) >= 2 and short_token == full_acronym:
        return True

    # City/location initials: KC -> Kansas City (Chiefs), LA -> Los Angeles.
    # Try every leading prefix except the full team name.
    for n in range(2, len(long_tokens)):
        lead_acronym = "".join(t[0] for t in long_tokens[:n])
        if short_token == lead_acronym:
            return True

    return False

def _smart_team_similarity(a: str, b: str, aliases: dict[str, str]) -> float:
    """
    Strict abbreviation-aware matching.

    Important: mascot equality alone is NOT enough. That caused false matches
    such as NHL Panthers/Knights being paired with unrelated NCAA teams.
    """
    x = _matcher_mod.canon(a, aliases)
    y = _matcher_mod.canon(b, aliases)
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    if x in y or y in x:
        return 0.97

    xt, yt = x.split(), y.split()

    # A Kalshi event can use only the team code while the sportsbook uses the
    # full franchise name. Sport-locking happens before this matcher, so a code
    # such as CHI is compared only inside the correct league.
    if len(xt) == 1 and _abbr_matches(xt[0], yt):
        return 0.95
    if len(yt) == 1 and _abbr_matches(yt[0], xt):
        return 0.95

    # Exact mascot phrase + credible city/team abbreviation.
    # Try one- and two-token mascot tails (e.g. Chiefs, Golden Knights).
    for tail_n in (2, 1):
        if len(xt) <= tail_n or len(yt) <= tail_n:
            continue
        if xt[-tail_n:] != yt[-tail_n:]:
            continue

        xlead = xt[:-tail_n]
        ylead = yt[:-tail_n]

        # Direct lead equality.
        if xlead == ylead:
            return 0.98

        # One side may be an abbreviation of the other's lead/full team tokens.
        if len(xlead) == 1 and _abbr_matches(xlead[0], ylead):
            return 0.96
        if len(ylead) == 1 and _abbr_matches(ylead[0], xlead):
            return 0.96

        # For VGK Golden Knights <-> Vegas Golden Knights, allow the
        # abbreviation to represent city + mascot initials.
        if len(xlead) == 1 and _abbr_matches(xlead[0], yt):
            return 0.95
        if len(ylead) == 1 and _abbr_matches(ylead[0], xt):
            return 0.95

    # Otherwise use ordinary fuzzy similarity without mascot-only boosting.
    return max(_rf_ratio(x, y), _rf_token_set_ratio(x, y)) / 100.0

_matcher_mod.team_similarity = _smart_team_similarity

STATE_PATH = Path(os.getenv("PM_EDGE_WATCH_STATE", ".pm_edge_watch_state.json"))

PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY", "").strip()
PUSHOVER_APP_TOKEN = os.getenv("PUSHOVER_APP_TOKEN", "").strip()
STREAMLIT_APP_URL = os.getenv("STREAMLIT_APP_URL", "").strip()

MIN_EDGE = float(os.getenv("PM_EDGE_MIN_NET_EDGE", "0.04"))
MODEL_BUFFER = float(os.getenv("PM_EDGE_MODEL_ERROR_BUFFER", "0.01"))
SLIPPAGE_BUFFER = float(os.getenv("PM_EDGE_SLIPPAGE_BUFFER", "0.0025"))
TEAM_PAIR_MIN_SAME_SPORT = float(os.getenv("PM_EDGE_WATCH_TEAM_PAIR_MIN_SAME_SPORT", "0.55"))
TEAM_PAIR_STRONG_SAME_SPORT = float(os.getenv("PM_EDGE_WATCH_TEAM_PAIR_STRONG_SAME_SPORT", "0.70"))
LOW_SCORE_MAX_TIME_DELTA_HOURS = float(os.getenv("PM_EDGE_WATCH_LOW_SCORE_MAX_TIME_DELTA_HOURS", "18"))
MATCH_MIN_UNKNOWN_SPORT = float(os.getenv("PM_EDGE_WATCH_MATCH_CONFIDENCE_UNKNOWN_SPORT", "0.72"))
DAILY_ODDS_BUDGET = int(os.getenv("PM_EDGE_ODDS_DAILY_BUDGET", "12"))
MAX_REFRESH_GROUPS = int(os.getenv("PM_EDGE_MAX_ODDS_REFRESH_PER_RUN", "3"))
EVENT_CACHE_HOURS = float(os.getenv("PM_EDGE_EVENT_CACHE_HOURS", "8"))
ALERT_COOLDOWN_HOURS = float(os.getenv("PM_EDGE_ALERT_COOLDOWN_HOURS", "6"))
MAX_ALERTS_PER_RUN = int(os.getenv("PM_EDGE_MAX_ALERTS_PER_RUN", "3"))
MAX_MARKETS = int(os.getenv("PM_EDGE_MAX_MARKETS", "3000"))
MAX_EVENT_HOURS = float(os.getenv("PM_EDGE_MAX_EVENT_HOURS", "96"))
MAX_AUTO_EDGE = float(os.getenv("PM_EDGE_MAX_AUTO_EDGE", "0.15"))
WATCHER_VERSION = 12

PRIMARY_MARKET = {
    "h2h": "h2h",
    "spread": "spreads",
    "total": "totals",
    "team_total": "team_totals",
}

ODDS_MARKETS_FOR_TYPE = {
    "h2h": "h2h",
    "spread": "spreads,alternate_spreads",
    "total": "totals,alternate_totals",
    "team_total": "team_totals,alternate_team_totals",
}

def now_utc():
    return datetime.now(timezone.utc)

def iso_now():
    return now_utc().isoformat()

def parse_dt(x):
    if not x:
        return None
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except Exception:
        return None

def age_hours(x):
    d = parse_dt(x)
    if not d:
        return 1e9
    return max(0.0, (now_utc() - d).total_seconds() / 3600.0)

def load_state():
    if not STATE_PATH.exists():
        return {
            "version": 1,
            "events_cache": {},
            "fair_cache": {},
            "quotes": {},
            "alerts": {},
            "daily_usage": {"date": "", "credits": 0},
            "first_run_notified": False,
        }
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        data.setdefault("events_cache", {})
        data.setdefault("fair_cache", {})
        data.setdefault("quotes", {})
        data.setdefault("alerts", {})
        data.setdefault("daily_usage", {"date": "", "credits": 0})
        data.setdefault("first_run_notified", False)
        return data
    except Exception:
        return {
            "version": 1,
            "events_cache": {},
            "fair_cache": {},
            "quotes": {},
            "alerts": {},
            "daily_usage": {"date": "", "credits": 0},
            "first_run_notified": False,
        }

def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True, default=str), encoding="utf-8")

def reset_daily_usage(state):
    today = now_utc().date().isoformat()
    du = state.setdefault("daily_usage", {})
    if du.get("date") != today:
        du["date"] = today
        du["credits"] = 0
    return du

def usage_cost(usage):
    raw = (usage or {}).get("requests_last")
    try:
        return max(0, int(float(raw)))
    except Exception:
        return 1

def cents(x):
    return f"{float(x)*100:.1f}¢"

def pct(x):
    return f"{float(x)*100:.1f}%"

def send_push(title, message, *, priority=0, url=None, url_title="Open PM Edge"):
    if not PUSHOVER_USER_KEY or not PUSHOVER_APP_TOKEN:
        print("PUSH SKIPPED: missing Pushover secrets")
        print(title)
        print(message)
        return False
    payload = {
        "token": PUSHOVER_APP_TOKEN,
        "user": PUSHOVER_USER_KEY,
        "title": title[:250],
        "message": message[:1024],
        "priority": str(priority),
    }
    target = url or STREAMLIT_APP_URL
    if target:
        payload["url"] = target
        payload["url_title"] = url_title
    r = requests.post("https://api.pushover.net/1/messages.json", data=payload, timeout=20)
    if r.status_code >= 400:
        print("Pushover error:", r.status_code, r.text[:500])
        return False
    print("Pushover sent:", title)
    return True

def market_move(market, prev):
    if not prev:
        return 0.0
    vals = []
    for k in ("yes_bid", "yes_ask", "no_bid", "no_ask"):
        a, b = market.get(k), prev.get(k)
        if a is not None and b is not None:
            vals.append(abs(float(a) - float(b)))
    return max(vals) if vals else 0.0

def event_hours(event):
    d = parse_dt((event or {}).get("commence_time"))
    if not d:
        return 9999.0
    return (d - now_utc()).total_seconds() / 3600.0

def fair_ttl_hours(event):
    h = event_hours(event)
    if h <= 3:
        return 1.5
    if h <= 12:
        return 3.0
    if h <= 36:
        return 6.0
    return 12.0

def priority_score(market, event, prev_quote, fair_entry):
    vol = max(0.0, float(market.get("volume_24h") or 0.0))
    score = min(40.0, math.log1p(vol) * 4.0)
    h = event_hours(event)
    if 0 <= h <= 3:
        score += 45
    elif 0 <= h <= 12:
        score += 32
    elif 0 <= h <= 36:
        score += 18
    elif 0 <= h <= 96:
        score += 8
    move = market_move(market, prev_quote)
    if move >= 0.03:
        score += 30
    elif move >= 0.02:
        score += 20
    elif move >= 0.01:
        score += 8
    if not fair_entry:
        score += 12
    return score

def get_events(odds, state):
    cache = state.setdefault("events_cache", {})
    if cache.get("events") and age_hours(cache.get("fetched_at")) <= EVENT_CACHE_HOURS:
        return cache["events"], 0

    sports, usage = odds.active_sports()
    events = []
    for sport in sports:
        key = sport.get("key")
        if not key:
            continue
        try:
            rows, _ = odds.events(key)
        except OddsAPIError:
            continue
        for e in rows:
            x = dict(e)
            x["_sport_key"] = key
            x["_sport_title"] = sport.get("title") or key
            x["_sport_group"] = sport.get("group") or ""
            events.append(x)

    cache["fetched_at"] = iso_now()
    cache["events"] = events
    return events, 0


def fetch_open_kalshi_sports_markets(base_url: str, max_events: int = 2500):
    """
    Load Kalshi OPEN EVENTS (not the generic market feed).

    Kalshi's /events endpoint excludes multivariate events. We request nested
    markets, then inject the event matchup title into each market before using
    the existing PM Edge parser. This avoids the general /markets feed being
    dominated by combo/MVE contracts and improves team matching.
    """
    out = []
    cursor = None
    seen_events = 0
    session = requests.Session()
    session.headers.update({"User-Agent": "PM-Edge-Watch/2.0"})
    base = base_url.rstrip("/")

    while seen_events < max_events:
        params = {
            "status": "open",
            "with_nested_markets": "true",
            "limit": min(200, max_events - seen_events),
        }
        if cursor:
            params["cursor"] = cursor

        r = session.get(base + "/events", params=params, timeout=25)
        r.raise_for_status()
        data = r.json()
        events_batch = data.get("events", [])
        if not isinstance(events_batch, list) or not events_batch:
            break

        for ev in events_batch:
            seen_events += 1
            category = str(ev.get("category") or "")
            # Include sports explicitly; if category is absent, let the sports parser decide.
            if category and "sport" not in category.lower():
                continue

            ev_title = str(ev.get("title") or "").strip()
            ev_sub = str(ev.get("sub_title") or "").strip()

            # Strip labels such as ": Spread" / ": Total Points" from the matchup.
            matchup = ev_title.split(":", 1)[0].strip() if ev_title else ""
            ev_markets = ev.get("markets") or []
            if not isinstance(ev_markets, list):
                continue

            for raw_market in ev_markets:
                if not isinstance(raw_market, dict):
                    continue

                # Ignore combo/multivariate children defensively.
                ticker = str(raw_market.get("ticker") or "")
                if ticker.startswith("KXMVE"):
                    continue

                enriched = dict(raw_market)
                original_title = str(raw_market.get("title") or "").strip()

                # Preserve the market's own occurrence_datetime.
                # Kalshi already provides this at market level and it is the
                # correct field for matching the real-world sports occurrence.

                # If the event provides a matchup, force it to be the parser's event prefix.
                # Example:
                # event.title  = "Denver vs Kansas City: Spread"
                # market.title = "Kansas City wins by over 2.5 points?"
                # becomes      = "Denver vs Kansas City: Kansas City wins by over 2.5 points?"
                lower_match = matchup.lower()
                if matchup and (
                    " vs " in lower_match or
                    " v " in lower_match or
                    " at " in lower_match or
                    " @ " in lower_match
                ):
                    enriched["title"] = f"{matchup}: {original_title}" if original_title else matchup

                nm = normalize_market(enriched)
                nm["_kalshi_event_title"] = ev_title
                nm["_kalshi_event_sub_title"] = ev_sub
                nm["_kalshi_category"] = category
                out.append(nm)

        cursor = data.get("cursor")
        if not cursor:
            break

    return out, seen_events



FUTURE_PHRASES = (
    "be the matchup in",
    "stanley cup final",
    "super bowl matchup",
    "world series matchup",
    "nba finals matchup",
    "championship matchup",
    "make the playoffs",
    "win the championship",
    "win the stanley cup",
    "win the super bowl",
    "win the world series",
    "season wins",
    "regular season wins",
    "total wins",
    "total points",
    "regular season",
    "season points",
    "most valuable player",
    "mvp",
    "rookie of the year",
    "coach of the year",
    "award",
)

FUTURE_TICKER_FRAGMENTS = (
    "WINS",
    "SEASONPTS",
    "SEASONPOINTS",
    "MVP",
    "CHAMP",
    "FINALS",
    "PLAYOFF",
    "AWARD",
    "ROY",
    "COTY",
)

def looks_like_future_or_outright(market: dict) -> bool:
    title = " ".join(
        str(market.get(k) or "")
        for k in ("title", "_kalshi_event_title", "_kalshi_event_sub_title")
    ).lower()

    ticker_blob = " ".join(
        str(market.get(k) or "")
        for k in ("ticker", "event_ticker")
    ).upper()

    if any(p in title for p in FUTURE_PHRASES):
        return True

    if any(fragment in ticker_blob for fragment in FUTURE_TICKER_FRAGMENTS):
        return True

    # Catch threshold-style season props even when phrased without "total wins".
    # Examples from live Kalshi data:
    # "win at least 60 games in the 2026-27 regular season"
    # "earn at least 70 points in the 2026-27 regular season"
    if "regular season" in title and any(
        phrase in title
        for phrase in ("at least", "more than", "fewer than", "less than", "games", "points")
    ):
        return True

    return False

def event_is_near_term(event: dict) -> bool:
    h = event_hours(event)
    # Require a real scheduled sportsbook event in the next few days.
    return -1.0 <= h <= MAX_EVENT_HOURS

def kalshi_market_is_near_term(market: dict) -> bool:
    kd = parse_dt(market.get("occurrence_datetime"))
    if not kd:
        # Some legitimate markets omit occurrence_datetime; let the sportsbook
        # event horizon and matcher confidence handle those instead.
        return True
    h = (kd - now_utc()).total_seconds() / 3600.0
    return -1.0 <= h <= MAX_EVENT_HOURS



def event_match_team_score(em) -> float:
    reason = str(getattr(em, "reason", "") or "")
    m = re.search(r"team=([0-9.]+)", reason)
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except Exception:
        return 0.0


def low_score_time_confirmed(market: dict, event: dict) -> tuple[bool, float | None]:
    """
    For weaker but same-sport team-name matches, require independent time agreement.
    """
    kd = parse_dt(market.get("occurrence_datetime"))
    sd = parse_dt((event or {}).get("commence_time"))
    if not kd or not sd:
        return False, None
    delta_h = abs((kd - sd).total_seconds()) / 3600.0
    return delta_h <= LOW_SCORE_MAX_TIME_DELTA_HOURS, delta_h

def inferred_sport_rule(market: dict):
    """
    Infer the sportsbook sport key from Kalshi's ticker/event ticker.
    Returns a predicate over The Odds API sport keys, or None if unknown.
    """
    blob = " ".join([
        str(market.get("ticker") or ""),
        str(market.get("event_ticker") or ""),
        str(market.get("_kalshi_event_title") or ""),
    ]).upper()

    exact = [
        (("NCAAF", "CFB"), "americanfootball_ncaaf"),
        (("NFL",), "americanfootball_nfl"),
        (("NCAAB", "CBB"), "basketball_ncaab"),
        (("WNBA",), "basketball_wnba"),
        (("NBA",), "basketball_nba"),
        (("MLB",), "baseball_mlb"),
        (("NHL",), "icehockey_nhl"),
        (("MLS",), "soccer_usa_mls"),
        (("EPL", "PREMIERLEAGUE"), "soccer_epl"),
        (("UCL", "CHAMPIONSLEAGUE"), "soccer_uefa_champs_league"),
        (("UFC", "MMA"), "mma_mixed_martial_arts"),
        (("BOXING",), "boxing_boxing"),
    ]
    for needles, key in exact:
        if any(n in blob for n in needles):
            return ("exact", key)

    # Tennis is split into many ATP/WTA tournament sport keys.
    if any(n in blob for n in ("TENNIS", "ATP", "WTA")):
        return ("prefix", "tennis_")

    # Golf also uses event-specific keys, though most outright markets are
    # filtered before this stage.
    if "GOLF" in blob or "PGA" in blob:
        return ("prefix", "golf_")

    return None

def sport_rule_matches(event: dict, rule) -> bool:
    if not rule:
        return True
    key = str(event.get("_sport_key") or "")
    mode, value = rule
    if mode == "exact":
        return key == value
    if mode == "prefix":
        return key.startswith(value)
    return True

def fair_to_dict(fair, event, spec, usage=None):
    return {
        "fetched_at": iso_now(),
        "fair_yes_prob": float(fair.fair_yes_prob),
        "source": fair.source,
        "confidence": float(fair.confidence),
        "n_books": int(fair.n_books),
        "dispersion": float(fair.dispersion),
        "raw_book_prob": fair.raw_book_prob,
        "model_prob": fair.model_prob,
        "detail": fair.detail,
        "event_id": event.get("id"),
        "sport_key": event.get("_sport_key"),
        "sport_title": event.get("_sport_title") or event.get("_sport_key") or "Sports",
        "market_type": spec.market_type,
        "selection": spec.selection,
        "point": spec.point,
        "commence_time": event.get("commence_time"),
        "requests_remaining": (usage or {}).get("requests_remaining"),
    }

def score_market(market, fair_entry, settings):
    tick = tick_size_for_price(market, market.get("yes_bid") or market.get("yes_ask") or 0.50)

    # Two-book alternate-line consensus is usable, but demand more edge.
    n_books = int(fair_entry.get("n_books") or 0)
    if n_books >= 4:
        required_edge = MIN_EDGE
    elif n_books == 3:
        required_edge = max(MIN_EDGE, 0.045)
    else:
        required_edge = max(MIN_EDGE, 0.06)

    return best_binary_trade(
        fair_yes_prob=float(fair_entry["fair_yes_prob"]),
        yes_bid=market.get("yes_bid"),
        yes_ask=market.get("yes_ask"),
        no_bid=market.get("no_bid"),
        no_ask=market.get("no_ask"),
        min_net_edge=required_edge,
        nonfee_buffer=MODEL_BUFFER + SLIPPAGE_BUFFER,
        maker_fee_rate=settings.maker_fee_rate,
        taker_fee_rate=settings.taker_fee_rate,
        tick_size=tick,
    )

def should_alert(state, market, fair_entry, q):
    key = f"{market['ticker']}|{q.side}"
    prev = state.setdefault("alerts", {}).get(key)
    if not prev:
        return True, "NEW"
    if not prev.get("active", False):
        return True, "BACK"
    old_entry = float(prev.get("entry_price") or 0)
    old_edge = float(prev.get("net_edge") or 0)
    if q.recommended_limit is not None and abs(float(q.recommended_limit) - old_entry) >= 0.02:
        return True, "PRICE"
    if q.net_edge is not None and float(q.net_edge) - old_edge >= 0.02:
        return True, "EDGE"
    if age_hours(prev.get("sent_at")) >= ALERT_COOLDOWN_HOURS:
        return True, "REMINDER"
    return False, ""

def record_alert(state, market, fair_entry, q):
    key = f"{market['ticker']}|{q.side}"
    state.setdefault("alerts", {})[key] = {
        "active": True,
        "sent_at": iso_now(),
        "entry_price": q.recommended_limit,
        "hard_max": q.hard_max,
        "net_edge": q.net_edge,
        "fair_yes_prob": fair_entry["fair_yes_prob"],
        "title": market.get("title"),
    }

def maybe_send_pass_alerts(state, market_by_ticker, fair_cache, settings):
    sent = 0
    for key, alert in list(state.setdefault("alerts", {}).items()):
        if not alert.get("active"):
            continue
        ticker, side = key.rsplit("|", 1)
        market = market_by_ticker.get(ticker)
        fair_entry = fair_cache.get(ticker)
        if not market or not fair_entry:
            continue
        # Do not make a pass decision from very stale fair value.
        event = {"commence_time": fair_entry.get("commence_time")}
        if age_hours(fair_entry.get("fetched_at")) > max(8.0, fair_ttl_hours(event) * 2):
            continue
        q = score_market(market, fair_entry, settings)
        if q.qualifies:
            continue
        msg = (
            f"{side} — {market.get('title','')[:150]}\n"
            f"Current quote no longer clears PM Edge's {MIN_EDGE:.0%} net-edge rule.\n"
            f"Status: PASS / DO NOT CHASE."
        )
        if send_push("⚠️ PM EDGE — PASS NOW", msg, priority=0):
            alert["active"] = False
            alert["pass_sent_at"] = iso_now()
            sent += 1
    return sent

def self_test():
    # No network: validate state/format behavior and threshold mechanics.
    s = {
        "alerts": {},
        "daily_usage": {"date": "", "credits": 0},
    }
    reset_daily_usage(s)
    assert s["daily_usage"]["date"] == now_utc().date().isoformat()
    assert cents(0.57) == "57.0¢"
    assert pct(0.061) == "6.1%"
    print("PM Edge watcher v12 self-test: PASS")
    return 0

def main():
    if "--self-test" in sys.argv:
        return self_test()

    if not os.getenv("ODDS_API_KEY", "").strip():
        raise SystemExit("Missing ODDS_API_KEY")
    settings = Settings()
    aliases = load_aliases()
    overlay = load_model_overlay()
    manual = load_manual_values()
    kalshi = KalshiPublicClient(settings.kalshi_base_url)
    odds = OddsAPIClient(settings.odds_api_key, settings.odds_regions)
    state = load_state()

    # v6 changes event-time semantics. Purge fair values and alerts produced by
    # older matcher versions so a stale future/false match can never alert again.
    if int(state.get("data_semantics_version", 0) or 0) != WATCHER_VERSION:
        state["fair_cache"] = {}
        state["alerts"] = {}
        state["quotes"] = {}
        state["data_semantics_version"] = WATCHER_VERSION

    daily = reset_daily_usage(state)

    markets, kalshi_events_seen = fetch_open_kalshi_sports_markets(
        settings.kalshi_base_url,
        max_events=MAX_MARKETS,
    )
    market_by_ticker = {m["ticker"]: m for m in markets}
    events, _ = get_events(odds, state)

    candidates = []
    supported_markets = 0
    event_matches_any_conf = 0
    futures_filtered = 0
    kalshi_horizon_filtered = 0
    sportsbook_horizon_filtered = 0

    sport_identified = 0
    same_sport_matches = 0
    accepted_same_sport = 0
    accepted_same_sport_strong = 0
    accepted_same_sport_time_confirmed = 0
    rejected_low_score_missing_time = 0
    rejected_low_score_bad_time = 0
    accepted_unknown_sport = 0
    top_same_sport_team_score = 0.0
    top_same_sport_confidence = 0.0
    best_confirmed_time_delta_hours = None

    for market in markets:
        # FIRST: eliminate season-long/futures markets before the generic text
        # parser can misread "win at least 60 games" as a head-to-head game.
        if looks_like_future_or_outright(market):
            futures_filtered += 1
            continue

        # SECOND: require the Kalshi occurrence itself to be near-term before
        # attempting any sportsbook event match.
        if not kalshi_market_is_near_term(market):
            kalshi_horizon_filtered += 1
            continue

        spec = parse_market(market)
        if spec.market_type not in PRIMARY_MARKET:
            continue
        supported_markets += 1

        sport_rule = inferred_sport_rule(market)
        if sport_rule:
            sport_identified += 1
            event_pool = [e for e in events if sport_rule_matches(e, sport_rule)]
        else:
            event_pool = events

        if not event_pool:
            continue

        # Never use close_time as a surrogate for kickoff. If occurrence_datetime
        # is absent, match_event will rely on participant identity.
        em = match_event(
            spec, event_pool, aliases,
            market.get("occurrence_datetime"),
        )

        team_score = event_match_team_score(em)
        if em.event:
            event_matches_any_conf += 1
            if sport_rule:
                same_sport_matches += 1
                top_same_sport_team_score = max(top_same_sport_team_score, team_score)
                top_same_sport_confidence = max(top_same_sport_confidence, float(em.confidence or 0))

        if not em.event:
            continue

        # Once the sport is identified, prefer direct participant-pair evidence
        # over the generic combined confidence score. match_event already rejects
        # ambiguous near-ties and only considers pair scores >= 0.55.
        if sport_rule:
            if team_score < TEAM_PAIR_MIN_SAME_SPORT:
                continue

            if team_score >= TEAM_PAIR_STRONG_SAME_SPORT:
                accepted_same_sport_strong += 1
            else:
                time_ok, delta_h = low_score_time_confirmed(market, em.event)
                if delta_h is None:
                    rejected_low_score_missing_time += 1
                    continue
                if not time_ok:
                    rejected_low_score_bad_time += 1
                    continue
                accepted_same_sport_time_confirmed += 1
                if best_confirmed_time_delta_hours is None or delta_h < best_confirmed_time_delta_hours:
                    best_confirmed_time_delta_hours = delta_h

            accepted_same_sport += 1
        else:
            if em.confidence < MATCH_MIN_UNKNOWN_SPORT:
                continue
            accepted_unknown_sport += 1

        if not event_is_near_term(em.event):
            sportsbook_horizon_filtered += 1
            continue

        cached = state.setdefault("fair_cache", {}).get(market["ticker"])
        prevq = state.setdefault("quotes", {}).get(market["ticker"])
        score = priority_score(market, em.event, prevq, cached)
        candidates.append((score, market, spec, em))

    candidates.sort(key=lambda x: x[0], reverse=True)

    # Update quote snapshot before scoring; cached fair values survive via Actions cache.
    for _, market, _, _ in candidates:
        state["quotes"][market["ticker"]] = {
            "seen_at": iso_now(),
            "yes_bid": market.get("yes_bid"),
            "yes_ask": market.get("yes_ask"),
            "no_bid": market.get("no_bid"),
            "no_ask": market.get("no_ask"),
        }

    # Refresh a small number of high-priority fair values each run.
    refreshed_groups = 0
    run_event_odds_cache = {}
    credits_remaining_today = max(0, DAILY_ODDS_BUDGET - int(daily.get("credits", 0)))

    fair_attempts = 0
    fair_successes = 0
    odds_errors = 0

    for _, market, spec, em in candidates:
        cached = state["fair_cache"].get(market["ticker"])
        ttl = fair_ttl_hours(em.event)
        if cached and age_hours(cached.get("fetched_at")) <= ttl:
            continue

        requested_markets = ODDS_MARKETS_FOR_TYPE.get(spec.market_type)
        if not requested_markets:
            continue

        group = (em.event.get("_sport_key"), em.event.get("id"), requested_markets)

        if group not in run_event_odds_cache:
            if refreshed_groups >= MAX_REFRESH_GROUPS or credits_remaining_today <= 0:
                continue
            try:
                event_odds, usage = odds.event_odds(group[0], group[1], group[2])
            except OddsAPIError as exc:
                odds_errors += 1
                print("Odds refresh failed:", group, exc)
                continue

            cost = usage_cost(usage)
            if cost > credits_remaining_today:
                continue

            credits_remaining_today -= cost
            daily["credits"] = int(daily.get("credits", 0)) + cost
            refreshed_groups += 1

            if isinstance(event_odds, dict):
                event_odds = dict(event_odds)
                event_odds["_sport_key"] = em.event.get("_sport_key")
                event_odds["_sport_title"] = em.event.get("_sport_title")
                event_odds["_sport_group"] = em.event.get("_sport_group")

            run_event_odds_cache[group] = (event_odds, usage)

        event_odds, usage = run_event_odds_cache[group]
        fair_attempts += 1

        # Featured moneylines should have broader coverage. Exact alternate lines
        # can legitimately have fewer books, so allow two but demand more edge later.
        min_books_required = 3 if spec.market_type == "h2h" else 2

        consensus = fair_from_event(
            spec, event_odds, aliases,
            min_books=min_books_required,
            max_dispersion=settings.odds_max_dispersion,
        )
        fair = blend_fair_value(market["ticker"], consensus, overlay)
        if fair:
            fair_successes += 1
            state["fair_cache"][market["ticker"]] = fair_to_dict(fair, em.event, spec, usage)

    # Manual fair values override any cache.
    for ticker, x in manual.items():
        state["fair_cache"][ticker] = {
            "fetched_at": iso_now(),
            "fair_yes_prob": x["fair_yes_prob"],
            "source": x["source"],
            "confidence": x["confidence"],
            "n_books": 0,
            "dispersion": 0.0,
            "raw_book_prob": None,
            "model_prob": None,
            "detail": x.get("notes", "manual"),
            "event_id": None,
            "sport_key": None,
            "sport_title": "Manual",
            "market_type": None,
            "selection": None,
            "point": None,
            "commence_time": None,
        }

    # Score cached, sufficiently fresh fair values.
    actionable = []
    for _, market, spec, em in candidates:
        fair_entry = state["fair_cache"].get(market["ticker"])
        if not fair_entry:
            continue
        # Avoid alerts from stale fair values.
        if age_hours(fair_entry.get("fetched_at")) > max(8.0, fair_ttl_hours(em.event) * 2):
            continue
        try:
            q = score_market(market, fair_entry, settings)
        except Exception:
            continue
        if not q.qualifies or q.recommended_limit is None:
            continue

        if looks_like_future_or_outright(market):
            continue
        if not kalshi_market_is_near_term(market):
            continue

        net_edge = float(q.net_edge or 0)
        # Extremely large "edges" in liquid sports markets are usually a
        # settlement/matching/data problem. Never auto-alert them.
        if net_edge > MAX_AUTO_EDGE:
            print(
                "SUSPICIOUS_EDGE_SKIPPED",
                market.get("ticker"),
                market.get("title"),
                q.side,
                q.recommended_limit,
                q.hard_max,
                net_edge,
            )
            continue

        actionable.append((net_edge, market, fair_entry, q))

    actionable.sort(key=lambda x: x[0], reverse=True)

    sent = 0
    for _, market, fair_entry, q in actionable:
        if sent >= MAX_ALERTS_PER_RUN:
            break
        yes_prob = float(fair_entry["fair_yes_prob"])
        side_prob = yes_prob if q.side == "YES" else 1.0 - yes_prob
        ok, reason = should_alert(state, market, fair_entry, q)
        if not ok:
            continue
        action = "BUY NOW" if q.execution == "TAKE" else "PLACE LIMIT"
        current_ask = q.best_ask
        sport = fair_entry.get("sport_title") or "Sports"
        msg = (
            f"{sport}\n"
            f"{q.side} — {market.get('title','')[:170]}\n\n"
            f"{action}: {cents(q.recommended_limit)}\n"
            f"DO NOT PAY OVER: {cents(q.hard_max)}\n"
            f"Current ask: {cents(current_ask) if current_ask is not None else '—'}\n"
            f"Fair chance: {pct(side_prob)}\n"
            f"Est. net edge: +{pct(q.net_edge)}\n"
            f"Books: {int(fair_entry.get('n_books') or 0)}\n\n"
            f"Do not chase above MAX."
        )
        title = f"🔥 PM EDGE +{pct(q.net_edge)} — {q.side}"
        if send_push(title, msg, priority=0):
            record_alert(state, market, fair_entry, q)
            sent += 1

    pass_sent = maybe_send_pass_alerts(state, market_by_ticker, state["fair_cache"], settings)

    # Health heartbeat confirms notifications and verifies the event matcher.
    if (
        not state.get("first_run_notified")
        or int(state.get("watcher_version_notified", 0) or 0) != WATCHER_VERSION
    ):
        remaining = None
        for x in state["fair_cache"].values():
            if x.get("requests_remaining") is not None:
                remaining = x.get("requests_remaining")
                break
        msg = (
            f"PM Edge Watch is running.\n"
            f"Kalshi open events checked: {kalshi_events_seen}\n"
            f"Sports markets loaded: {len(markets)}\n"
            f"Supported game markets parsed: {supported_markets}\n"
            f"Futures/outrights filtered: {futures_filtered}\n"
            f"Sport identified: {sport_identified}\n"
            f"Event matches found: {event_matches_any_conf}\n"
            f"Same-sport matches: {same_sport_matches}\n"
            f"Accepted same-sport: {accepted_same_sport}\n"
            f"  strong-name: {accepted_same_sport_strong}\n"
            f"  time-confirmed: {accepted_same_sport_time_confirmed}\n"
            f"Top same-sport team score: {top_same_sport_team_score:.2f}\n"
            f"Top same-sport confidence: {top_same_sport_confidence:.2f}\n"
            f"Low-score missing-time rejects: {rejected_low_score_missing_time}\n"
            f"Low-score bad-time rejects: {rejected_low_score_bad_time}\n"
            f"Kalshi horizon filtered: {kalshi_horizon_filtered}\n"
            f"Sportsbook horizon filtered: {sportsbook_horizon_filtered}\n"
            f"Near-term candidates: {len(candidates)}\n"
            f"Fair-value attempts: {fair_attempts}\n"
            f"Fair values created: {fair_successes}\n"
            f"Fair values cached: {len(state['fair_cache'])}\n"
            f"Odds request errors: {odds_errors}\n"
            f"Odds credits used today: {daily.get('credits',0)}/{DAILY_ODDS_BUDGET}"
        )
        if remaining is not None:
            msg += f"\nOdds API account credits remaining: {remaining}"
        if send_push("✅ PM Edge Watch v12 is live", msg, priority=0):
            state["first_run_notified"] = True
            state["watcher_version_notified"] = WATCHER_VERSION

    # Prune old state.
    live_tickers = set(market_by_ticker)
    state["quotes"] = {k:v for k,v in state["quotes"].items() if k in live_tickers}
    state["fair_cache"] = {
        k:v for k,v in state["fair_cache"].items()
        if k in live_tickers and age_hours(v.get("fetched_at")) <= 72
    }

    save_state(state)
    print(json.dumps({
        "kalshi_events_seen": kalshi_events_seen,
        "markets": len(markets),
        "sportsbook_events": len(events),
        "supported_markets": supported_markets,
        "futures_filtered": futures_filtered,
        "sport_identified": sport_identified,
        "event_matches_any_conf": event_matches_any_conf,
        "same_sport_matches": same_sport_matches,
        "accepted_same_sport": accepted_same_sport,
        "accepted_same_sport_strong": accepted_same_sport_strong,
        "accepted_same_sport_time_confirmed": accepted_same_sport_time_confirmed,
        "rejected_low_score_missing_time": rejected_low_score_missing_time,
        "rejected_low_score_bad_time": rejected_low_score_bad_time,
        "accepted_unknown_sport": accepted_unknown_sport,
        "top_same_sport_team_score": round(top_same_sport_team_score, 4),
        "top_same_sport_confidence": round(top_same_sport_confidence, 4),
        "best_confirmed_time_delta_hours": None if best_confirmed_time_delta_hours is None else round(best_confirmed_time_delta_hours, 3),
        "kalshi_horizon_filtered": kalshi_horizon_filtered,
        "sportsbook_horizon_filtered": sportsbook_horizon_filtered,
        "candidates": len(candidates),
        "fair_attempts": fair_attempts,
        "fair_successes": fair_successes,
        "odds_errors": odds_errors,
        "fair_cache": len(state["fair_cache"]),
        "actionable": len(actionable),
        "alerts_sent": sent,
        "pass_alerts_sent": pass_sent,
        "odds_credits_today": daily.get("credits", 0),
        "daily_budget": DAILY_ODDS_BUDGET,
    }, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
